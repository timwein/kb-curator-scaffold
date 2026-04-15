"""
Playwright scraper for X/Twitter bookmarks.

AUTHENTICATION STRATEGY
-----------------------
X aggressively blocks Playwright-controlled browsers at the login step, so
instead of logging into X via Playwright, we reuse your EXISTING X session
from your regular Chrome:

  1. You're already logged into X in your daily Chrome
  2. browser_cookie3 reads X's session cookies from Chrome's cookie jar
  3. We inject those cookies into Playwright's browser context
  4. Playwright navigates to bookmarks with a valid session already attached
  5. No login form, no bot detection on the login step

If your X session expires in the future, just log back into X in your
regular Chrome and the next run picks up the fresh cookies automatically.

FIRST-RUN NOTES
---------------
- On macOS, the first invocation may show a Keychain prompt asking for
  access to "Chrome Safe Storage". Click Allow. It's remembered.
- If you get a PermissionError reading Chrome's cookie database, your
  terminal may need Full Disk Access:
    System Settings → Privacy & Security → Full Disk Access → add Terminal
- Chrome does NOT need to be closed — browser_cookie3 handles a running
  Chrome by snapshotting the cookie DB.

LIMITATIONS (v1)
----------------
- Threads are flagged (is_thread: true) but NOT fully followed — only the
  root tweet's text is captured. The agent is instructed to note this.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


BOOKMARKS_URL = "https://x.com/i/bookmarks"

# Selectors — rely on data-testid attributes X uses consistently. Update
# here if scraping breaks.
TWEET_ARTICLE = 'article[data-testid="tweet"]'
TWEET_TEXT = 'div[data-testid="tweetText"]'

# X "Article" (longform) selectors. On an Article's status page the essay
# body is NOT inside tweetText — it's rendered in a separate React subtree
# with its own testids. Verified live against x.com in April 2026.
#   - twitter-article-title: the headline shown above the essay
#   - twitterArticleRichTextView: the essay body, clean of UI chrome
# (twitterArticleReadView is the outer container but includes engagement
# counts, and longformRichTextComponent is a child of the RichTextView
# with identical content — picking the outer testid is more robust.)
ARTICLE_TITLE = 'div[data-testid="twitter-article-title"]'
ARTICLE_BODY = 'div[data-testid="twitterArticleRichTextView"]'

# Matches the author handle + tweet ID in a status permalink href.
STATUS_HREF_RE = re.compile(r"^/([^/]+)/status/(\d+)")

# Stealth args to reduce automation detection. Not bulletproof but helps.
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
]

# Init script to hide navigator.webdriver, the most common automation flag.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


def import_x_cookies_from_chrome() -> list[dict[str, Any]]:
    """Read X/Twitter cookies from the user's real Chrome cookie jar and
    return them in Playwright's expected format.

    Raises RuntimeError with a helpful message if cookies can't be read or
    no X session is present.
    """
    try:
        import browser_cookie3
    except ImportError as e:
        raise RuntimeError(
            "browser_cookie3 not installed. Run: pip install browser-cookie3"
        ) from e

    try:
        jar = browser_cookie3.chrome()
    except Exception as e:
        raise RuntimeError(
            f"Could not read Chrome cookies: {e}\n"
            "Hints:\n"
            "  - On macOS, a Keychain prompt may appear the first time; click Allow\n"
            "  - Make sure you're logged into X in your regular Chrome\n"
            "  - If this keeps failing, your terminal may need Full Disk Access:\n"
            "    System Settings → Privacy & Security → Full Disk Access → add Terminal"
        ) from e

    def is_x_domain(domain: str) -> bool:
        """True only for x.com / twitter.com and their subdomains — NOT for
        unrelated domains like launchx.com or thisisdax.com that just happen
        to contain 'x.com' as a substring."""
        d = (domain or "").lower().lstrip(".")
        return (
            d == "x.com"
            or d == "twitter.com"
            or d.endswith(".x.com")
            or d.endswith(".twitter.com")
        )

    playwright_cookies: list[dict[str, Any]] = []
    for c in jar:
        if not c.domain or not is_x_domain(c.domain):
            continue

        cookie: dict[str, Any] = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": False,
            "sameSite": "Lax",
        }
        if c.expires:
            try:
                cookie["expires"] = float(c.expires)
            except (TypeError, ValueError):
                pass
        playwright_cookies.append(cookie)

    if not playwright_cookies:
        raise RuntimeError(
            "No X/Twitter cookies found in your Chrome cookie jar.\n"
            "Open Chrome, log in to x.com, visit your bookmarks page once to\n"
            "make sure the session is active, then retry."
        )

    return playwright_cookies


def _launch_context(p, profile_dir: Path, headless: bool):
    """Launch a persistent Chrome context with stealth args + init script."""
    # Remove stale lock files left by crashed previous runs.
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (profile_dir / lock).unlink(missing_ok=True)

    context = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=STEALTH_ARGS,
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    return context


def fetch_bookmarks(
    chrome_user_data_dir: str | Path,
    max_items: int = 100,
    headless: bool = True,
    since_iso: str | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Fetch bookmarked tweets by reusing the X session from your real Chrome.

    Returns a list of dicts:
      {
        "tweet_id": str,
        "author": str,              # "@handle"
        "url": str,                 # "https://x.com/handle/status/..."
        "text": str,
        "tweet_datetime": str,      # ISO-8601, from <time datetime>
        "media_alt": list[str]|None,
        "external_url": str|None,   # t.co outbound link (if link-share tweet)
        "is_thread": bool,
      }

    Args:
      max_items: scrape at most this many UNFILTERED items (before since_iso
        is applied). Use a high number for backfill runs. The bookmarks page
        is ordered by bookmark date, not tweet date, so we can't early-terminate
        based on date — we have to scan everything up to this cap.
      since_iso: if set (e.g. "2026-01-01"), drop items whose tweet_datetime
        is strictly earlier. Applied after scraping.
      verbose: print scroll progress every N items (useful for big backfills).
    """
    profile_dir = Path(chrome_user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("importing X cookies from your real Chrome...", flush=True)
    x_cookies = import_x_cookies_from_chrome()
    print(f"  imported {len(x_cookies)} X cookies", flush=True)

    with sync_playwright() as p:
        context = _launch_context(p, profile_dir, headless)
        context.add_cookies(x_cookies)

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(BOOKMARKS_URL, wait_until="domcontentloaded")

        try:
            page.wait_for_selector(TWEET_ARTICLE, timeout=15000)
        except Exception:
            context.close()
            raise RuntimeError(
                "Bookmarks page did not load any tweets. Your X session may "
                "have expired. Open Chrome, log back into x.com, reload your "
                "bookmarks page once, then retry."
            )

        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        stalled_rounds = 0
        prev_count = 0

        while len(items) < max_items and stalled_rounds < 6:
            articles = page.query_selector_all(TWEET_ARTICLE)
            for art in articles:
                parsed = _parse_tweet(art)
                if parsed and parsed["tweet_id"] not in seen_ids:
                    seen_ids.add(parsed["tweet_id"])
                    items.append(parsed)
                    if len(items) >= max_items:
                        break

            if verbose and len(items) != prev_count and len(items) % 25 == 0:
                print(f"  ... scraped {len(items)} so far", flush=True)

            if len(items) == prev_count:
                stalled_rounds += 1
            else:
                stalled_rounds = 0
            prev_count = len(items)

            page.mouse.wheel(0, 2500)
            time.sleep(1.5)

        context.close()

    if since_iso:
        filtered = [
            it for it in items if (it.get("tweet_datetime") or "") >= since_iso
        ]
        print(
            f"  scraped {len(items)} total; {len(filtered)} match since>={since_iso}",
            flush=True,
        )
        return filtered

    return items


def enrich_full_text(
    bookmarks: list[dict[str, Any]],
    chrome_user_data_dir: str | Path,
    headless: bool = True,
    delay_seconds: float = 1.5,
) -> None:
    """Visit each bookmark's permalink and replace truncated `text` with
    the full tweet body. Also extracts X Article (longform) content when
    present. Mutates `bookmarks` in place.

    The bookmarks-page scrape only captures the preview (~280 chars) that
    X renders before "Show more". Tweet detail pages render the full body,
    so we re-visit each permalink to recover it.

    For tweets posted as X Articles (longform essays), the bookmark card
    shows only a cover image and the status page renders the essay in a
    separate subtree (ARTICLE_TITLE / ARTICLE_BODY selectors). When an
    Article is detected we also set `article_title` and `article_body` on
    the bookmark, and replace `text` with `"<title>\\n\\n<body>"` so
    downstream code doesn't need to know about Articles.

    Only overwrites text when the new version is strictly longer than the
    existing scrape — a failed fetch, a shorter render, or a rate-limit
    fallback page can't lose data. Permalinks that can't be loaded are
    silently skipped; the agent's web-search backfill covers those.
    """
    if not bookmarks:
        return

    profile_dir = Path(chrome_user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    x_cookies = import_x_cookies_from_chrome()

    enriched = 0
    skipped = 0

    with sync_playwright() as p:
        context = _launch_context(p, profile_dir, headless)
        context.add_cookies(x_cookies)

        for i, b in enumerate(bookmarks):
            url = b.get("url")
            tweet_id = b.get("tweet_id", "?")
            if not url:
                skipped += 1
                continue
            page = None
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector(TWEET_ARTICLE, timeout=10000)
                # First <article> on a tweet detail page is the primary
                # tweet. Replies come after; quote-tweets nest as children.
                article = page.query_selector(TWEET_ARTICLE)
                if article:
                    did_enrich = False

                    # Standard tweet body.
                    text_el = article.query_selector(TWEET_TEXT)
                    if text_el:
                        full_text = text_el.inner_text().strip()
                        current = b.get("text") or ""
                        if len(full_text) > len(current):
                            b["text"] = full_text
                            did_enrich = True

                    # X Article (longform). Rendered in a separate subtree
                    # from tweetText — a tweet can have one, the other, or
                    # neither (media-only). Check independently.
                    body_el = article.query_selector(ARTICLE_BODY)
                    if body_el:
                        article_body = body_el.inner_text().strip()
                        title_el = article.query_selector(ARTICLE_TITLE)
                        article_title = (
                            title_el.inner_text().strip() if title_el else None
                        )
                        if article_body:
                            b["article_body"] = article_body
                            b["article_title"] = article_title
                            merged = (
                                f"{article_title}\n\n{article_body}"
                                if article_title
                                else article_body
                            )
                            current = b.get("text") or ""
                            if len(merged) > len(current):
                                b["text"] = merged
                                did_enrich = True

                    if did_enrich:
                        enriched += 1
                    else:
                        skipped += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"  (enrich skipped {tweet_id}: {e})", flush=True)
                skipped += 1
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

            # Pace requests to avoid rate-limit patterns.
            if i < len(bookmarks) - 1:
                time.sleep(delay_seconds)

        context.close()

    print(
        f"  enriched {enriched}, unchanged/skipped {skipped}",
        flush=True,
    )


def _parse_tweet(article) -> dict[str, Any] | None:
    """Pull fields out of a single <article data-testid="tweet"> element."""
    try:
        text_el = article.query_selector(TWEET_TEXT)
        text = text_el.inner_text().strip() if text_el else ""

        # Permalink is the <a> wrapping a <time> element.
        status_link_el = article.query_selector("a:has(time)")
        if not status_link_el:
            return None
        href = status_link_el.get_attribute("href") or ""
        m = STATUS_HREF_RE.match(href)
        if not m:
            return None
        author_handle = m.group(1)
        tweet_id = m.group(2)

        # Tweet creation timestamp from the <time> element's datetime attr.
        tweet_datetime = ""
        time_el = status_link_el.query_selector("time")
        if time_el:
            tweet_datetime = time_el.get_attribute("datetime") or ""

        show_thread = article.query_selector('a:has-text("Show this thread")')
        is_thread = show_thread is not None

        # For media-only / link-share tweets, capture fallback signal:
        #   - media_alt: alt text from embedded images ("Article cover image", etc.)
        #   - external_url: the t.co outbound link (for link shares — the agent
        #     can web_fetch this to get the linked article content)
        media_alts: list[str] = []
        for img in article.query_selector_all("img[alt]") or []:
            alt = (img.get_attribute("alt") or "").strip()
            if alt and alt.lower() not in ("image", "", author_handle.lower()):
                media_alts.append(alt)

        external_url: str | None = None
        for a in article.query_selector_all("a[href]") or []:
            link = a.get_attribute("href") or ""
            if link.startswith("https://t.co/"):
                external_url = link
                break

        return {
            "tweet_id": tweet_id,
            "author": f"@{author_handle}",
            "url": f"https://x.com{href}",
            "tweet_datetime": tweet_datetime,
            "text": text,
            "media_alt": media_alts or None,
            "external_url": external_url,
            "is_thread": is_thread,
        }
    except Exception:
        return None


def diagnose_cookies() -> None:
    """Print a summary of which X cookies would be imported, for debugging."""
    try:
        cookies = import_x_cookies_from_chrome()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return
    print(f"Found {len(cookies)} X/Twitter cookies in Chrome:")
    key_names = {"auth_token", "ct0", "guest_id", "twid", "kdt"}
    for c in cookies:
        marker = "*" if c["name"] in key_names else " "
        print(f"  {marker} {c['domain']:20s} {c['name']}")
    has_auth = any(c["name"] == "auth_token" for c in cookies)
    if not has_auth:
        print()
        print("WARNING: no 'auth_token' cookie found. You may not be logged in.")
        print("Open Chrome, log in to x.com, reload bookmarks, then retry.")
    else:
        print()
        print("auth_token present — session looks good.")


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print which X cookies would be imported, without launching browser",
    )
    parser.add_argument(
        "--profile",
        default="./chrome-profile",
        help="Playwright Chrome profile directory (default: ./chrome-profile)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=20,
        help="Max bookmarks to scrape (default: 20 for testing)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run with a visible browser (useful for debugging)",
    )
    args = parser.parse_args()

    if args.diagnose:
        diagnose_cookies()
    else:
        items = fetch_bookmarks(
            args.profile, max_items=args.max, headless=not args.no_headless
        )
        print(_json.dumps(items, indent=2, ensure_ascii=False))
        print(f"\nTotal: {len(items)} bookmarks", flush=True)
