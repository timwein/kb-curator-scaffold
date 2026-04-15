"""
Playwright scraper for the X Following + For You feeds.

Companion to lib/fetcher.py (which scrapes /i/bookmarks). Shares the
cookie-reuse + stealth-Chromium pattern via the import_x_cookies_from_chrome
and _launch_context helpers.

WHAT THIS CAPTURES
------------------
Only *thread-root original tweets* from the user's feed. Explicitly excluded:
  - Retweets (the tweet_id is the original's; bookmarking re-bookmarks
    someone else's post and would be confusing).
  - Replies (non-root items — "Replying to @X" banner present).
  - Promoted / ad tweets (socialContext = "Ad" or similar).

For X Articles (longform essays), tweet text in the feed is typically
empty — we still capture them because the article-body lookup happens
later during the tweet-kb ingestion pass.

OUTPUT
------
A list of candidate dicts, each:
    {
      "tweet_id": "1234567890",
      "author": "@handle",
      "url": "https://x.com/handle/status/1234567890",
      "text": "preview text (may be empty for Articles)",
      "tweet_datetime": "2026-04-14T15:30:00.000Z",
      "is_article": bool,           # heuristic: twitter-article-title present
      "source_feed": "following" | "for_you",
    }

Deduped within a run by tweet_id (the same tweet can appear in both
feeds — first-seen wins, source_feed records whichever found it first).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from lib.fetcher import (
    TWEET_ARTICLE,
    TWEET_TEXT,
    ARTICLE_TITLE,
    STATUS_HREF_RE,
    _launch_context,
    import_x_cookies_from_chrome,
)


# X's home timeline has two tabs — "For you" (algorithmic) and "Following"
# (chronological). Both render at /home; the tab switch is client-side. So
# for Following we navigate to /home and then click the Following tab.
# (Note: /following is a separate page showing the list of accounts the
# user follows, NOT the chronological feed — don't use it here.)
FEED_URL = "https://x.com/home"

# The Following tab is rendered as a role="tab" element inside the primary
# column. X historically used both an <a href="/following"> and a <div
# role="tab"> depending on the UI revision — we'll try a few selectors
# in order.
FOLLOWING_TAB_SELECTORS = [
    'a[role="tab"][href="/following"]',
    'div[role="tab"]:has-text("Following")',
    'a[role="tab"]:has-text("Following")',
]

# Selectors used to detect non-root items (for exclusion).
#
# socialContext is the banner above a tweet in a feed. It's present for:
#   - Retweets:  "<handle> reposted"
#   - Promoted:  "Ad"
#   - Pinned:    "Pinned"
#   - Follow suggestions from conversations
#
# For replies, the tweet itself has an inReplyTo link rendered inside
# the article — we detect replies by querying for an anchor with the
# Replying to treatment, which uses text content rather than a stable
# testid.
SOCIAL_CONTEXT = 'div[data-testid="socialContext"]'


def _parse_feed_tweet(article, source_feed: str) -> dict[str, Any] | None:
    """Extract candidate fields from one feed tweet card. Returns None if
    the card should be excluded (retweet, reply, promoted, or malformed)."""
    try:
        # --- Exclusion: retweet / promoted ---
        #
        # X uses socialContext for the small grey line above a tweet card
        # in feeds. On retweets it contains "reposted"; on promoted it's
        # "Ad"; on pinned it's "Pinned Tweet". We conservatively drop all
        # socialContext-tagged cards because none of them represent
        # "root tweet the feed is currently surfacing for fresh signal."
        #
        # Exception: "Pinned" is fine in principle (it's a root tweet), but
        # pinned tweets persist for weeks/months and re-surface every run,
        # flooding the considered-log. Easier to just skip them.
        if article.query_selector(SOCIAL_CONTEXT) is not None:
            ctx_text = (
                article.query_selector(SOCIAL_CONTEXT).inner_text().strip().lower()
            )
            if any(
                marker in ctx_text
                for marker in ("reposted", "ad", "pinned", "promoted")
            ):
                return None

        # --- Extract canonical fields ---
        status_link_el = article.query_selector("a:has(time)")
        if not status_link_el:
            return None
        href = status_link_el.get_attribute("href") or ""
        m = STATUS_HREF_RE.match(href)
        if not m:
            return None
        author_handle = m.group(1)
        tweet_id = m.group(2)

        # --- Exclusion: reply (non-root thread item) ---
        #
        # Feed cards for replies render a "Replying to @X" line above the
        # tweet text. Hunting for the exact text is brittle because the
        # handle varies; a stabler signal is that this text is inside
        # the tweet's main body container, NOT in socialContext.
        #
        # We check for an anchor that links to a status AND whose own
        # textContent starts with "Replying to". If such an anchor exists
        # inside the article, this card is a reply, drop it.
        reply_indicator = article.query_selector(
            'div:has-text("Replying to")'
        )
        if reply_indicator is not None:
            # Guard against false positives: the user's own quoted-tweet
            # preview area can contain the phrase. Only drop if the match
            # is in the card's header area (before the main tweetText or
            # article body).
            text_el = article.query_selector(TWEET_TEXT)
            if text_el is None or _is_before(reply_indicator, text_el):
                return None

        # Tweet datetime
        tweet_datetime = ""
        time_el = status_link_el.query_selector("time")
        if time_el:
            tweet_datetime = time_el.get_attribute("datetime") or ""

        # Preview text (may be empty for Articles)
        text_el = article.query_selector(TWEET_TEXT)
        text = text_el.inner_text().strip() if text_el else ""

        # Article detection — the feed card for an Article shows the title.
        is_article = article.query_selector(ARTICLE_TITLE) is not None

        return {
            "tweet_id": tweet_id,
            "author": f"@{author_handle}",
            "url": f"https://x.com{href}",
            "tweet_datetime": tweet_datetime,
            "text": text,
            "is_article": is_article,
            "source_feed": source_feed,
        }
    except Exception:
        return None


def _is_before(node_a, node_b) -> bool:
    """True if node_a appears before node_b in document order.

    Playwright ElementHandles don't expose document position directly, so
    we use a bounding-box y comparison: if A is visibly above B in the
    card, A is "before" in reading order. Good enough for reply detection,
    where "Replying to" always renders at the top of the card.
    """
    try:
        box_a = node_a.bounding_box()
        box_b = node_b.bounding_box()
        if box_a is None or box_b is None:
            return False
        return box_a["y"] < box_b["y"]
    except Exception:
        return False


def _switch_to_following_tab(page) -> bool:
    """Click the Following tab on /home. Returns True on success.

    Tries each selector in FOLLOWING_TAB_SELECTORS in order. If none
    match or the click fails, logs and returns False — the caller should
    treat this as "no Following feed this run" rather than crashing.
    """
    for selector in FOLLOWING_TAB_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el is None:
                continue
            el.click(timeout=5000)
            # Give the feed a moment to re-render with Following content.
            time.sleep(2.0)
            return True
        except Exception:
            continue
    print("  WARN: could not find/click Following tab", flush=True)
    return False


def _scrape_feed(
    page,
    source_feed: str,
    max_items: int,
    scroll_rounds: int,
    verbose: bool,
) -> list[dict[str, Any]]:
    """Scrape up to max_items candidate tweets from the currently-loaded feed.

    Caller is responsible for navigating + switching to the right tab
    before calling this."""
    try:
        page.wait_for_selector(TWEET_ARTICLE, timeout=15000)
    except Exception:
        print(
            f"  WARN: {source_feed} did not render any tweets within 15s — skipping",
            flush=True,
        )
        return []

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    stalled_rounds = 0
    prev_count = 0

    for _ in range(scroll_rounds):
        articles = page.query_selector_all(TWEET_ARTICLE)
        for art in articles:
            parsed = _parse_feed_tweet(art, source_feed)
            if parsed and parsed["tweet_id"] not in seen_ids:
                seen_ids.add(parsed["tweet_id"])
                items.append(parsed)
                if len(items) >= max_items:
                    break
        if verbose:
            print(
                f"    {source_feed}: {len(items)} candidates after "
                f"{len(articles)} cards on-screen",
                flush=True,
            )
        if len(items) >= max_items:
            break

        # Stalled detection: if two rounds pass without new unique tweets,
        # we've hit the feed's end or X has stopped loading more.
        if len(items) == prev_count:
            stalled_rounds += 1
            if stalled_rounds >= 2:
                break
        else:
            stalled_rounds = 0
        prev_count = len(items)

        page.mouse.wheel(0, 2500)
        time.sleep(1.5)

    return items


def fetch_feeds(
    chrome_user_data_dir: str | Path,
    max_candidates: int = 100,
    scroll_rounds: int = 8,
    headless: bool = True,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Scrape both Following + For You feeds and return deduped candidates.

    The function splits the budget evenly across feeds (max_candidates/2 per
    feed), then unions. Later dedup vs ingested.jsonl + bookmark-considered.jsonl
    happens in the orchestrator.

    Args:
        chrome_user_data_dir: Playwright profile dir (same as ingestion fetcher).
        max_candidates: overall cap on returned candidates (default 100).
        scroll_rounds: how many scroll-and-scrape rounds per feed (default 8).
        headless: default True for automated runs; False for debugging.
        verbose: print per-feed progress lines.

    Returns:
        List of candidate dicts (see module docstring for shape). Order:
        Following candidates first (chronological), then For You candidates.
        First-seen-wins dedup on tweet_id.
    """
    profile_dir = Path(chrome_user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    per_feed_cap = max(10, max_candidates // 2)

    print("importing X cookies from your real Chrome...", flush=True)
    x_cookies = import_x_cookies_from_chrome()
    print(f"  imported {len(x_cookies)} X cookies", flush=True)

    combined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with sync_playwright() as p:
        context = _launch_context(p, profile_dir, headless)
        context.add_cookies(x_cookies)

        page = context.pages[0] if context.pages else context.new_page()

        # --- Navigate to /home once; switch tabs client-side for each feed. ---
        print(f"navigating to {FEED_URL}...", flush=True)
        page.goto(FEED_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(TWEET_ARTICLE, timeout=15000)
        except Exception:
            print(
                "  WARN: /home did not render any tweets within 15s — aborting",
                flush=True,
            )
            context.close()
            return []

        # /home defaults to "For you" — scrape that first (no tab click needed).
        for source_feed in ("for_you", "following"):
            if source_feed == "following":
                print("  switching to Following tab...", flush=True)
                if not _switch_to_following_tab(page):
                    print("  skipping Following feed", flush=True)
                    continue
                # Re-wait for the Following feed to render fresh tweets.
                try:
                    page.wait_for_selector(TWEET_ARTICLE, timeout=10000)
                except Exception:
                    print(
                        "  WARN: Following tab did not render tweets — skipping",
                        flush=True,
                    )
                    continue

            print(f"scraping {source_feed} feed...", flush=True)
            feed_items = _scrape_feed(
                page=page,
                source_feed=source_feed,
                max_items=per_feed_cap,
                scroll_rounds=scroll_rounds,
                verbose=verbose,
            )
            print(f"  {source_feed}: {len(feed_items)} candidates", flush=True)
            for it in feed_items:
                if it["tweet_id"] in seen_ids:
                    continue
                seen_ids.add(it["tweet_id"])
                combined.append(it)
                if len(combined) >= max_candidates:
                    break

            if len(combined) >= max_candidates:
                break

            # Scroll back to top before the next feed so the tab click and
            # initial scrape start from a clean state.
            try:
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(0.5)
            except Exception:
                pass

        context.close()

    print(
        f"combined candidates: {len(combined)} "
        f"(capped at max_candidates={max_candidates})",
        flush=True,
    )
    return combined


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", default="./chrome-profile", help="Playwright Chrome profile dir"
    )
    parser.add_argument("--max", type=int, default=40, help="Max candidates (both feeds combined)")
    parser.add_argument(
        "--scroll-rounds", type=int, default=4, help="Scroll rounds per feed"
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run with a visible browser (useful for debugging)",
    )
    args = parser.parse_args()

    items = fetch_feeds(
        chrome_user_data_dir=args.profile,
        max_candidates=args.max,
        scroll_rounds=args.scroll_rounds,
        headless=not args.no_headless,
        verbose=True,
    )
    print()
    print(_json.dumps(items, indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(items)} candidates")
