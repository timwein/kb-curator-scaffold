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

THREAD HANDLING
---------------
When a bookmark is part of a self-thread (the author wrote multiple
sequential tweets in a chain), enrich_full_text walks UP to the root
of the thread and then crawls DOWN, collecting all same-author tweets.
The bookmark's `text` is rewritten to the full concatenated thread
(with a `[bookmarked tweet]` marker so downstream code knows which one
the user actually saved), `is_thread` is set to True, and
`thread_tweets` lists each tweet's id/url/text/datetime.

Capped at 15 tweets per thread to keep agent context manageable. Stops
crawling after 5 consecutive non-author replies (X groups self-thread
continuations together before other replies).
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
        "text": str,                # full thread body after enrich_full_text;
                                    # bookmarked tweet marked with "[bookmarked tweet]"
        "tweet_datetime": str,      # ISO-8601, from <time datetime>
        "media_alt": list[str]|None,
        "external_url": str|None,   # t.co outbound link (if link-share tweet)
        "is_thread": bool,          # True after enrich_full_text if a self-thread
                                    # was detected and >=2 same-author tweets captured
        "thread_tweets": list|None, # populated by enrich_full_text when a thread is
                                    # detected. Each entry: {tweet_id, url, text,
                                    # tweet_datetime}, ordered chronologically.
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
    max_thread_tweets: int = 15,
) -> None:
    """Visit each bookmark's permalink and:
      - Replace truncated `text` with the full tweet body.
      - Extract X Article (longform) content when present.
      - Walk same-author self-threads: hop up to the root, then crawl down
        collecting sequential same-author tweets. The bookmark's `text` is
        rewritten to the full concatenated thread (with the bookmarked
        tweet marked), `is_thread` is set True, and `thread_tweets` lists
        each tweet in the thread in chronological order.

    Mutates `bookmarks` in place.

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

    `max_thread_tweets` caps the number of tweets collected per thread.
    Default 15 keeps agent context manageable; bump for completeness if
    your threads regularly run longer.
    """
    if not bookmarks:
        return

    profile_dir = Path(chrome_user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    x_cookies = import_x_cookies_from_chrome()

    enriched = 0
    skipped = 0
    threads_found = 0

    with sync_playwright() as p:
        context = _launch_context(p, profile_dir, headless)
        context.add_cookies(x_cookies)

        for i, b in enumerate(bookmarks):
            tweet_id = b.get("tweet_id", "?")
            try:
                focal_updated, thread_found = _enrich_one(
                    context, b, max_thread_tweets=max_thread_tweets
                )
                if focal_updated:
                    enriched += 1
                else:
                    skipped += 1
                if thread_found:
                    threads_found += 1
                    n = len(b.get("thread_tweets") or [])
                    print(
                        f"  thread captured for {tweet_id}: {n} tweets",
                        flush=True,
                    )
            except Exception as e:
                print(f"  (enrich skipped {tweet_id}: {e})", flush=True)
                skipped += 1

            # Pace requests to avoid rate-limit patterns.
            if i < len(bookmarks) - 1:
                time.sleep(delay_seconds)

        context.close()

    print(
        f"  enriched {enriched}, unchanged/skipped {skipped}, "
        f"threads found {threads_found}",
        flush=True,
    )


def _extract_full_tweet_text(article) -> str:
    """Read the full body of an article on a status page.

    Prefers the X Article (longform) subtree when present — for those
    tweets the standard tweetText div is empty and the essay lives in
    twitterArticleRichTextView. Falls back to tweetText for normal tweets.
    """
    body_el = article.query_selector(ARTICLE_BODY)
    if body_el:
        article_body = body_el.inner_text().strip()
        if article_body:
            title_el = article.query_selector(ARTICLE_TITLE)
            title = title_el.inner_text().strip() if title_el else None
            return f"{title}\n\n{article_body}" if title else article_body

    text_el = article.query_selector(TWEET_TEXT)
    return text_el.inner_text().strip() if text_el else ""


def _enrich_one(
    context,
    bookmark: dict[str, Any],
    max_thread_tweets: int = 15,
    max_walk_up_hops: int = 5,
    max_scroll_rounds: int = 8,
    consecutive_non_author_stop: int = 5,
) -> tuple[bool, bool]:
    """Enrich one bookmark in place. Returns (focal_text_updated, thread_found).

    Strategy:
      1. Open the bookmark's permalink. Extract focal full text (and X
         Article body if present) — that's the existing enrichment.
      2. Walk UP: if same-author tweets appear above the focal in DOM
         order, those are thread parents. Navigate to the topmost one and
         repeat — at most `max_walk_up_hops` times. Stops when no same-
         author parents remain (= root reached) or we revisit a URL.
      3. Open the root's status page and scroll down, collecting same-
         author articles in DOM order. Stop after we hit
         `consecutive_non_author_stop` non-author articles in a row (X
         renders self-thread continuations contiguously before other
         replies), `max_thread_tweets` total, or `max_scroll_rounds`
         stalled scrolls.
      4. If we found >1 same-author tweets including the focal, rewrite
         the bookmark's `text` to the full concatenated thread (with a
         [bookmarked tweet] marker), set is_thread, and attach
         thread_tweets.
    """
    url = bookmark.get("url")
    bookmark_tweet_id = bookmark.get("tweet_id")
    author_handle_lower = (bookmark.get("author") or "").lstrip("@").lower()

    if not url or not bookmark_tweet_id or not author_handle_lower:
        return (False, False)

    # --- Step 1+2: enrich focal text + walk up to root. ---
    current_url = url
    current_tweet_id = bookmark_tweet_id
    visited_hops: set[str] = set()
    root_url = url
    root_tweet_id = bookmark_tweet_id
    focal_text_updated = False

    for hop in range(max_walk_up_hops):
        if current_url in visited_hops:
            break
        visited_hops.add(current_url)

        page = context.new_page()
        try:
            try:
                page.goto(current_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector(TWEET_ARTICLE, timeout=10000)
            except Exception:
                # If the first hop fails, treat it as a skipped enrichment.
                # If a later hop fails, we've already recorded the previous
                # hop's tweet as a candidate root; bail with that.
                break

            articles = page.query_selector_all(TWEET_ARTICLE)
            parsed_list: list[tuple[Any, dict[str, Any]]] = []
            for art in articles:
                p_dict = _parse_tweet(art)
                if p_dict:
                    parsed_list.append((art, p_dict))

            focal_idx = next(
                (
                    idx
                    for idx, (_, p_dict) in enumerate(parsed_list)
                    if p_dict["tweet_id"] == current_tweet_id
                ),
                None,
            )
            if focal_idx is None:
                # Page didn't render the tweet we expected — bail.
                break

            # First hop is the bookmarked tweet — enrich focal text here.
            if hop == 0:
                focal_art, _ = parsed_list[focal_idx]
                full_text = _extract_full_tweet_text(focal_art)
                current_text = bookmark.get("text") or ""
                if full_text and len(full_text) > len(current_text):
                    bookmark["text"] = full_text
                    focal_text_updated = True
                # Also expose the X Article subtree fields when present,
                # to keep parity with the legacy enrichment contract.
                body_el = focal_art.query_selector(ARTICLE_BODY)
                if body_el:
                    article_body = body_el.inner_text().strip()
                    if article_body:
                        title_el = focal_art.query_selector(ARTICLE_TITLE)
                        bookmark["article_body"] = article_body
                        bookmark["article_title"] = (
                            title_el.inner_text().strip() if title_el else None
                        )

            # Look for same-author tweets in DOM order ABOVE the focal —
            # those are thread parents.
            same_author_above = [
                p_dict
                for (_, p_dict) in parsed_list[:focal_idx]
                if p_dict["author"].lstrip("@").lower() == author_handle_lower
            ]
            if not same_author_above:
                root_url = current_url
                root_tweet_id = current_tweet_id
                break

            # Walk to the topmost same-author parent (earliest in DOM order).
            topmost = same_author_above[0]
            current_url = topmost["url"]
            current_tweet_id = topmost["tweet_id"]
        finally:
            try:
                page.close()
            except Exception:
                pass

    # --- Step 3: at root, scroll down and collect same-author tweets. ---
    collected: dict[str, dict[str, Any]] = {}
    page = context.new_page()
    try:
        try:
            page.goto(root_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector(TWEET_ARTICLE, timeout=10000)
        except Exception:
            return (focal_text_updated, False)

        # Brief settle for lazily-loaded replies.
        time.sleep(1.0)

        prev_collected = 0
        stalled_rounds = 0
        for _ in range(max_scroll_rounds):
            articles = page.query_selector_all(TWEET_ARTICLE)

            seen_root = False
            consecutive_non_author = 0
            stop_now = False

            for art in articles:
                p_dict = _parse_tweet(art)
                if not p_dict:
                    continue
                tid = p_dict["tweet_id"]
                same_author = (
                    p_dict["author"].lstrip("@").lower() == author_handle_lower
                )

                if tid == root_tweet_id:
                    seen_root = True
                    if tid not in collected:
                        collected[tid] = {
                            "tweet_id": tid,
                            "url": p_dict["url"],
                            "text": _extract_full_tweet_text(art),
                            "tweet_datetime": p_dict.get("tweet_datetime", ""),
                            "author": p_dict["author"],
                        }
                    consecutive_non_author = 0
                    continue

                if not seen_root:
                    # Parent context above the root — we already walked up,
                    # so anything above root here isn't part of our thread
                    # collection scope.
                    continue

                if same_author:
                    if tid not in collected:
                        collected[tid] = {
                            "tweet_id": tid,
                            "url": p_dict["url"],
                            "text": _extract_full_tweet_text(art),
                            "tweet_datetime": p_dict.get("tweet_datetime", ""),
                            "author": p_dict["author"],
                        }
                    consecutive_non_author = 0
                    if len(collected) >= max_thread_tweets:
                        stop_now = True
                        break
                else:
                    consecutive_non_author += 1
                    if consecutive_non_author >= consecutive_non_author_stop:
                        stop_now = True
                        break

            if stop_now:
                break

            if len(collected) == prev_collected:
                stalled_rounds += 1
                if stalled_rounds >= 3:
                    break
            else:
                stalled_rounds = 0
            prev_collected = len(collected)

            page.mouse.wheel(0, 2500)
            time.sleep(1.5)
    finally:
        try:
            page.close()
        except Exception:
            pass

    # Need the focal tweet present + at least one continuation to call it
    # a thread. Otherwise leave the bookmark as a normal single-tweet enrich.
    if bookmark_tweet_id not in collected or len(collected) <= 1:
        return (focal_text_updated, False)

    # If our step-1 focal extraction got more text than the root-page
    # render did (focal page has the tweet expanded), prefer it.
    focal_enriched_text = bookmark.get("text") or ""
    if len(focal_enriched_text) > len(collected[bookmark_tweet_id]["text"]):
        collected[bookmark_tweet_id]["text"] = focal_enriched_text

    # tweet_id is a snowflake — numerically ascending == chronological.
    thread = sorted(collected.values(), key=lambda t: int(t["tweet_id"]))
    thread = thread[:max_thread_tweets]

    parts = []
    for t in thread:
        if t["tweet_id"] == bookmark_tweet_id:
            parts.append(f"[bookmarked tweet]\n{t['text']}")
        else:
            parts.append(t["text"])
    bookmark["text"] = "\n\n---\n\n".join(parts)
    bookmark["is_thread"] = True
    bookmark["thread_tweets"] = [
        {
            "tweet_id": t["tweet_id"],
            "url": t["url"],
            "text": t["text"],
            "tweet_datetime": t["tweet_datetime"],
        }
        for t in thread
    ]

    return (True, True)


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
    parser.add_argument(
        "--test-thread",
        metavar="URL",
        help="Test thread collection on a single bookmark URL — runs the "
             "walk-to-root + crawl logic and prints the resulting bookmark "
             "object. Skips the bookmarks-list scrape.",
    )
    args = parser.parse_args()

    if args.diagnose:
        diagnose_cookies()
    elif args.test_thread:
        # Build a synthetic single-bookmark record from the URL so we can
        # exercise enrich_full_text without touching /i/bookmarks.
        m = STATUS_HREF_RE.search(args.test_thread.split("x.com", 1)[-1])
        if not m:
            import sys as _sys
            _sys.exit(
                f"Doesn't look like an x.com status URL: {args.test_thread!r}"
            )
        synthetic = {
            "tweet_id": m.group(2),
            "author": f"@{m.group(1)}",
            "url": args.test_thread,
            "text": "",
            "tweet_datetime": "",
            "media_alt": None,
            "external_url": None,
            "is_thread": False,
        }
        enrich_full_text(
            [synthetic],
            chrome_user_data_dir=args.profile,
            headless=not args.no_headless,
        )
        print(_json.dumps(synthetic, indent=2, ensure_ascii=False))
    else:
        items = fetch_bookmarks(
            args.profile, max_items=args.max, headless=not args.no_headless
        )
        print(_json.dumps(items, indent=2, ensure_ascii=False))
        print(f"\nTotal: {len(items)} bookmarks", flush=True)
