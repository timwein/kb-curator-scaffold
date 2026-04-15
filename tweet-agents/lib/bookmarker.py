"""
Playwright action: click the Bookmark button on a specific tweet URL.

Used by run_bookmarker.py to execute the actual X bookmark write action
when the CMA bookmark-agent calls its `bookmark_tweet` custom tool.
Keeping this local-side (rather than letting the agent container hold
the user's session) matches the "credentials host-side" pattern from
managed-agents-client-patterns.md #9.

SAFETY INVARIANT
----------------
This function will NEVER click the `removeBookmark` testid. That button
un-bookmarks a tweet. We only click `bookmark` (add state). If we don't
find the add-state button, we either report "already_bookmarked" (if the
remove-state button is present, meaning the user already bookmarked it
manually or in a prior run) or "failed" (if neither button is visible —
could be a deleted tweet, protected account, or an X UI change).

REUSE
-----
Shares the cookie-reuse + stealth-Chromium pattern from lib/fetcher.py
via import_x_cookies_from_chrome + _launch_context.

CLASS-STYLE USAGE
-----------------
Bookmarking N tweets in one run is much cheaper with a single persistent
Playwright context than N fresh contexts. The orchestrator creates one
BookmarkerSession via `with BookmarkerSession(...) as bm:` and calls
`bm.bookmark(url)` per tweet.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from lib.fetcher import (
    TWEET_ARTICLE,
    _launch_context,
    import_x_cookies_from_chrome,
)


# The two testids on the bookmark button. Verified 2026-04 via DevTools
# Console dump on @annimaniac's article page, where the tweet was already
# bookmarked and the button showed data-testid="removeBookmark".
BOOKMARK_ADD = 'button[data-testid="bookmark"]'
BOOKMARK_REMOVE = 'button[data-testid="removeBookmark"]'


class BookmarkerSession:
    """A reusable Playwright session for performing multiple bookmark
    clicks in one run. Use as a context manager."""

    def __init__(
        self,
        chrome_user_data_dir: str | Path,
        headless: bool = True,
        inter_bookmark_delay: float = 1.5,
    ):
        self.profile_dir = Path(chrome_user_data_dir).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.inter_bookmark_delay = inter_bookmark_delay
        self._pw = None
        self._context = None
        self._last_action_at: float | None = None

    def __enter__(self) -> "BookmarkerSession":
        self._pw = sync_playwright().start()
        cookies = import_x_cookies_from_chrome()
        self._context = _launch_context(self._pw, self.profile_dir, self.headless)
        self._context.add_cookies(cookies)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()

    def bookmark(self, tweet_url: str) -> dict[str, Any]:
        """Click the bookmark button on the given tweet URL.

        Returns a dict:
            {"status": "bookmarked",         "detail": "..."}   # success
            {"status": "already_bookmarked", "detail": "..."}   # no-op
            {"status": "failed",             "detail": "..."}   # error

        Never raises (orchestrator relies on this to always return a
        shaped result).
        """
        if self._context is None:
            return {"status": "failed", "detail": "BookmarkerSession not entered"}

        # Pace requests to avoid rate-limit patterns.
        if self._last_action_at is not None:
            elapsed = time.monotonic() - self._last_action_at
            if elapsed < self.inter_bookmark_delay:
                time.sleep(self.inter_bookmark_delay - elapsed)

        page = None
        try:
            page = self._context.new_page()
            try:
                page.goto(tweet_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                return {
                    "status": "failed",
                    "detail": f"navigation error: {type(e).__name__}: {e}",
                }

            # Wait for the tweet article to render. If it doesn't, the
            # tweet may have been deleted or is behind a protected account.
            try:
                page.wait_for_selector(TWEET_ARTICLE, timeout=10000)
            except Exception:
                return {
                    "status": "failed",
                    "detail": "tweet article did not render (deleted, protected, or rate-limited)",
                }

            # Scope ALL button lookups to the primary article element so
            # we never accidentally click a button in a nested quote-tweet
            # or a reply below.
            article = page.query_selector(TWEET_ARTICLE)
            if article is None:
                return {"status": "failed", "detail": "primary article disappeared"}

            # SAFETY CHECK #1: if the remove-bookmark button is present,
            # the tweet is already bookmarked. Report and bail — do NOT
            # click.
            remove_btn = article.query_selector(BOOKMARK_REMOVE)
            if remove_btn is not None:
                return {
                    "status": "already_bookmarked",
                    "detail": "removeBookmark testid present — tweet was already bookmarked",
                }

            # SAFETY CHECK #2: find the add-bookmark button. If missing,
            # X may have changed its selectors or the tweet is in an
            # unusual state. Bail safely.
            add_btn = article.query_selector(BOOKMARK_ADD)
            if add_btn is None:
                return {
                    "status": "failed",
                    "detail": f"neither {BOOKMARK_ADD!r} nor {BOOKMARK_REMOVE!r} found",
                }

            # Click it.
            try:
                add_btn.click(timeout=5000)
            except Exception as e:
                return {
                    "status": "failed",
                    "detail": f"click error: {type(e).__name__}: {e}",
                }

            # Verify: after the click, the button should have flipped to
            # removeBookmark. If it didn't, the click didn't take effect
            # (possibly a rate-limit / transient X error).
            try:
                page.wait_for_selector(
                    f'article[data-testid="tweet"] {BOOKMARK_REMOVE}',
                    timeout=5000,
                )
            except Exception:
                return {
                    "status": "failed",
                    "detail": "click fired but button did not flip to removeBookmark",
                }

            return {"status": "bookmarked", "detail": "button flipped to removeBookmark"}

        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            self._last_action_at = time.monotonic()


def bookmark_one(
    tweet_url: str,
    chrome_user_data_dir: str | Path = "./chrome-profile",
    headless: bool = True,
) -> dict[str, Any]:
    """Convenience one-shot for CLI/testing. Opens a fresh session, bookmarks
    the given URL, closes. For multi-tweet runs, use BookmarkerSession."""
    with BookmarkerSession(chrome_user_data_dir, headless=headless) as s:
        return s.bookmark(tweet_url)


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Full tweet URL, e.g., https://x.com/handle/status/123...")
    parser.add_argument("--profile", default="./chrome-profile")
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser (useful for debugging selector mismatches)",
    )
    args = parser.parse_args()

    result = bookmark_one(
        args.url,
        chrome_user_data_dir=args.profile,
        headless=not args.no_headless,
    )
    print(_json.dumps(result, indent=2))
