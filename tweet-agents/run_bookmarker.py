"""
HOURLY RUNTIME ORCHESTRATOR for the bookmarking agent.

Flow:
  1. Scrape Following + For You via lib.feed_fetcher.
  2. Dedup against meta/ingested.jsonl + _system/profile/bookmark-considered.jsonl on GitHub.
  3. Create a CMA session mounting the KB repo; send the kickoff with candidates.
  4. Stream events. On agent.custom_tool_use for `bookmark_tweet`:
       - Enforce per-run budget (max 10).
       - Enforce confidence floor (skip silently if < 0.7).
       - Otherwise call lib.bookmarker.BookmarkerSession.bookmark(url) locally.
       - Respond with user.custom_tool_result so the agent can continue.
  5. After session idles: append every considered candidate to
     _system/profile/bookmark-considered.jsonl (so next run's dedup excludes them),
     commit, push.

Runs via launchd hourly 06:00-00:00 PT. Single-run; no continuous loop.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export GITHUB_PAT=github_pat_...
    python run_bookmarker.py
    python run_bookmarker.py --dry-run   # don't actually bookmark, just show what the agent picks
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import anthropic

from lib.bookmark_prompts import (
    BUDGET_PER_RUN,
    CONFIDENCE_FLOOR,
    build_kickoff_message,
)
from lib.bookmarker import BookmarkerSession
from lib.feed_fetcher import fetch_feeds


CONFIG_PATH = Path(__file__).parent / "config.json"
CONSIDERED_LOG_PATH_IN_REPO = "_system/profile/bookmark-considered.jsonl"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"ERROR: {CONFIG_PATH} not found. Run setup.py + setup_bookmarker.py first.")
    config = json.loads(CONFIG_PATH.read_text())
    placeholder = (
        not config.get("bookmark_agent_id")
        or str(config.get("bookmark_agent_id", "")).startswith("agent_REPLACE")
        or not config.get("bookmark_environment_id")
        or str(config.get("bookmark_environment_id", "")).startswith("env_REPLACE")
    )
    if placeholder:
        sys.exit(
            "ERROR: config.json has bookmark-agent placeholder IDs.\n"
            "Run: python setup_bookmarker.py"
        )
    return config


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"ERROR: {name} is not set in the environment.")
    return val


def github_fetch_raw(pat: str, repo_url: str, path_in_repo: str) -> str | None:
    """Fetch a file's raw contents from the KB repo. Returns None on 404."""
    repo_path = repo_url.removeprefix("https://github.com/").rstrip("/")
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{path_in_repo}"
    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.raw",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def load_dedup_ids(pat: str, repo_url: str) -> set[str]:
    """Union of tweet_ids already in meta/ingested.jsonl and
    _system/profile/bookmark-considered.jsonl."""
    ids: set[str] = set()
    for path in ("meta/ingested.jsonl", CONSIDERED_LOG_PATH_IN_REPO):
        content = github_fetch_raw(pat, repo_url, path)
        if content is None:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["tweet_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return ids


def stream_run(
    client: anthropic.Anthropic,
    config: dict,
    github_pat: str,
    candidates: list[dict[str, Any]],
    now_iso: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Run one CMA session, handle bookmark_tweet custom-tool calls, return
    the list of per-candidate consideration records."""

    candidates_by_id = {c["tweet_id"]: c for c in candidates}
    considered: dict[str, dict[str, Any]] = {}  # tweet_id -> record
    bookmark_count = 0

    # Prepare the local Playwright session for bookmarking. We create it
    # eagerly (not on first tool call) so the browser is warm — the first
    # bookmark click is ~5s slower if Chrome has to boot fresh.
    if dry_run:
        bookmarker_ctx: Any = _DryRunBookmarker()
    else:
        bookmarker_ctx = BookmarkerSession(
            chrome_user_data_dir=config["chrome_user_data_dir"],
            headless=True,
        )

    with bookmarker_ctx as bm:
        print(f"creating CMA session for {len(candidates)} candidates...", flush=True)
        session = client.beta.sessions.create(
            agent={
                "type": "agent",
                "id": config["bookmark_agent_id"],
                "version": int(config["bookmark_agent_version"]),
            },
            environment_id=config["bookmark_environment_id"],
            title=f"bookmarker {now_iso[:16]}",
            resources=[
                {
                    "type": "github_repository",
                    "url": config["github_repo_url"],
                    "authorization_token": github_pat,
                    "mount_path": config["mount_path"],
                    "checkout": {
                        "type": "branch",
                        "name": config["github_repo_branch"],
                    },
                }
            ],
        )
        print(f"  session: {session.id}", flush=True)

        kickoff = build_kickoff_message(items=candidates, now_iso=now_iso)

        try:
            with client.beta.sessions.events.stream(session_id=session.id) as stream:
                client.beta.sessions.events.send(
                    session_id=session.id,
                    events=[
                        {
                            "type": "user.message",
                            "content": [{"type": "text", "text": kickoff}],
                        }
                    ],
                )

                for event in stream:
                    etype = getattr(event, "type", None)

                    if etype == "agent.message":
                        for block in getattr(event, "content", None) or []:
                            if getattr(block, "type", None) == "text":
                                print(block.text, end="", flush=True)
                        print()

                    elif etype == "agent.thinking":
                        print("  [thinking...]", flush=True)

                    elif etype == "agent.tool_use":
                        # Built-in tool (read, grep, glob) — agent is scanning KB.
                        print(f"  [tool: {getattr(event, 'name', '?')}]", flush=True)

                    elif etype == "agent.custom_tool_use":
                        tool_name = getattr(event, "tool_name", None) or getattr(
                            event, "name", None
                        )
                        tool_input = getattr(event, "input", None) or {}
                        tool_use_id = getattr(event, "id", None)

                        if tool_name == "bookmark_tweet":
                            response, counted, record = _handle_bookmark_tool(
                                tool_input=tool_input,
                                candidates_by_id=candidates_by_id,
                                bookmarker=bm,
                                bookmark_count=bookmark_count,
                                now_iso=now_iso,
                            )
                            if counted:
                                bookmark_count += 1
                            if record is not None:
                                considered[record["tweet_id"]] = record
                            _send_tool_result(
                                client,
                                session.id,
                                tool_use_id=tool_use_id,
                                content=response,
                            )
                        else:
                            # Unknown custom tool — shouldn't happen, but be safe.
                            _send_tool_result(
                                client,
                                session.id,
                                tool_use_id=tool_use_id,
                                content={"error": f"unknown tool: {tool_name}"},
                                is_error=True,
                            )

                    elif etype == "session.status_terminated":
                        print("\n[session terminated]", flush=True)
                        break

                    elif etype == "session.status_idle":
                        stop_reason = getattr(event, "stop_reason", None)
                        stop_type = (
                            getattr(stop_reason, "type", None) if stop_reason else None
                        )
                        if stop_type == "requires_action":
                            # Waiting on a tool result — we've already sent it.
                            continue
                        print(f"\n[session idle — {stop_type}]", flush=True)
                        break

                    elif etype == "session.error":
                        print(f"\n[SESSION ERROR] {event}", file=sys.stderr, flush=True)
                        break
        finally:
            try:
                client.beta.sessions.archive(session_id=session.id)
            except Exception as e:
                print(f"(archive deferred: {e})", flush=True)

    # Fill in "not bookmarked" records for every candidate the agent did NOT
    # call the tool on. Purpose: dedup log must cover every candidate we
    # sent to the agent, not just the ones it chose, so we never re-show
    # the same candidate next run.
    for tid, cand in candidates_by_id.items():
        if tid in considered:
            continue
        considered[tid] = {
            "tweet_id": tid,
            "author": cand.get("author"),
            "url": cand.get("url"),
            "source_feed": cand.get("source_feed"),
            "considered_at": now_iso,
            "bookmarked": False,
            "confidence": None,
            "reason": "agent did not select",
            "outcome": "not_selected",
        }

    return list(considered.values())


def _handle_bookmark_tool(
    tool_input: dict[str, Any],
    candidates_by_id: dict[str, dict[str, Any]],
    bookmarker: Any,
    bookmark_count: int,
    now_iso: str,
) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
    """Dispatch one bookmark_tweet call.

    Returns (response_to_agent, counted_toward_budget, consideration_record).
    `counted` is True only when the orchestrator actually bookmarked (not
    over-budget and not sub-confidence).
    """
    tweet_id = str(tool_input.get("tweet_id") or "").strip()
    author = str(tool_input.get("author") or "").strip()
    reason = str(tool_input.get("reason") or "").strip()
    try:
        confidence = float(tool_input.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    cand = candidates_by_id.get(tweet_id)
    url = cand["url"] if cand else f"https://x.com/i/status/{tweet_id}"
    source_feed = cand.get("source_feed") if cand else None

    base_record = {
        "tweet_id": tweet_id,
        "author": author or (cand["author"] if cand else None),
        "url": url,
        "source_feed": source_feed,
        "considered_at": now_iso,
        "confidence": confidence,
        "reason": reason,
    }

    # Guard: tweet_id must be in the candidate list (agents shouldn't
    # invent IDs, but catch it if they do).
    if cand is None:
        print(
            f"  bookmark_tweet: UNKNOWN tweet_id {tweet_id!r} — rejecting",
            flush=True,
        )
        return (
            {
                "status": "rejected",
                "reason": "tweet_id not in the candidate list you were sent. Do not invent IDs.",
            },
            False,
            {**base_record, "bookmarked": False, "outcome": "rejected_unknown_id"},
        )

    # Guard: budget cap.
    if bookmark_count >= BUDGET_PER_RUN:
        print(
            f"  bookmark_tweet {tweet_id}: BUDGET EXHAUSTED ({bookmark_count}/{BUDGET_PER_RUN})",
            flush=True,
        )
        return (
            {
                "status": "budget_exhausted",
                "reason": (
                    f"Per-run budget of {BUDGET_PER_RUN} bookmarks is used up. "
                    "Stop calling bookmark_tweet and finish your turn."
                ),
            },
            False,
            {
                **base_record,
                "bookmarked": False,
                "outcome": "budget_exhausted",
            },
        )

    # Guard: confidence floor.
    if confidence < CONFIDENCE_FLOOR:
        print(
            f"  bookmark_tweet {tweet_id}: SKIPPED (confidence {confidence:.2f} < {CONFIDENCE_FLOOR})",
            flush=True,
        )
        return (
            {
                "status": "skipped_low_confidence",
                "feedback": (
                    f"Not bookmarked — confidence {confidence:.2f} is below the floor "
                    f"of {CONFIDENCE_FLOOR}. If you're not confident, skip the tweet."
                ),
            },
            False,
            {
                **base_record,
                "bookmarked": False,
                "outcome": "skipped_low_confidence",
            },
        )

    # Execute the click.
    print(
        f"  bookmark_tweet {tweet_id} ({author}, conf={confidence:.2f}): clicking...",
        flush=True,
    )
    result = bookmarker.bookmark(url)
    status = result.get("status", "unknown")
    print(f"    -> {status}: {result.get('detail', '')}", flush=True)

    counted = status == "bookmarked"
    outcome = (
        "bookmarked"
        if status == "bookmarked"
        else ("already_bookmarked" if status == "already_bookmarked" else "failed")
    )

    return (
        {"status": status, "detail": result.get("detail", "")},
        counted,
        {**base_record, "bookmarked": counted, "outcome": outcome},
    )


class _DryRunBookmarker:
    """Stand-in for BookmarkerSession under --dry-run. Doesn't touch X."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def bookmark(self, url: str) -> dict[str, Any]:
        return {"status": "bookmarked", "detail": "(dry-run — no click performed)"}


def _send_tool_result(
    client: anthropic.Anthropic,
    session_id: str,
    tool_use_id: str,
    content: dict[str, Any],
    is_error: bool = False,
) -> None:
    event: dict[str, Any] = {
        "type": "user.custom_tool_result",
        "custom_tool_use_id": tool_use_id,
        "content": [{"type": "text", "text": json.dumps(content)}],
    }
    if is_error:
        event["is_error"] = True
    client.beta.sessions.events.send(session_id=session_id, events=[event])


def append_considered_log_and_push(
    github_pat: str,
    repo_url: str,
    repo_branch: str,
    new_records: list[dict[str, Any]],
) -> None:
    """Shallow-clone the KB repo, append new records to
    _system/profile/bookmark-considered.jsonl, commit + push."""
    if not new_records:
        print("no considered records to append", flush=True)
        return

    workdir = Path(tempfile.mkdtemp(prefix="bookmarker-log-"))
    try:
        url_with_pat = repo_url.replace(
            "https://", f"https://x-access-token:{github_pat}@"
        )
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", repo_branch, url_with_pat, str(workdir)],
            check=True,
            text=True,
        )
        # Set user.email to an email GitHub has verified on your account.
        # This matters because Vercel Hobby refuses to deploy commits whose
        # committer can't be associated with a GitHub user. Keep the name
        # distinct so bookmarker commits are still visually identifiable in
        # git log / GitHub UI.
        git_email = os.environ.get("GIT_COMMITTER_EMAIL")
        if not git_email:
            raise SystemExit(
                "GIT_COMMITTER_EMAIL env var must be set to a "
                "GitHub-verified email on your account."
            )
        subprocess.run(
            ["git", "config", "user.email", git_email],
            cwd=str(workdir),
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "tweet-bookmarker"],
            cwd=str(workdir),
            check=True,
        )
        log_path = workdir / CONSIDERED_LOG_PATH_IN_REPO
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            for rec in new_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        bookmarked_count = sum(1 for r in new_records if r.get("bookmarked"))
        commit_msg = (
            f"bookmarker run: {bookmarked_count} bookmarked, "
            f"{len(new_records) - bookmarked_count} considered-not-bookmarked"
        )
        subprocess.run(
            ["git", "add", CONSIDERED_LOG_PATH_IN_REPO],
            cwd=str(workdir),
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(workdir),
            check=True,
        )
        # Pull-rebase then push, to handle the (rare) race with the
        # ingestion agent pushing concurrently.
        for attempt in range(3):
            try:
                subprocess.run(
                    ["git", "pull", "--rebase"],
                    cwd=str(workdir),
                    check=True,
                )
                subprocess.run(
                    ["git", "push", "origin", repo_branch],
                    cwd=str(workdir),
                    check=True,
                )
                break
            except subprocess.CalledProcessError as e:
                print(f"  push attempt {attempt+1} failed: {e}", flush=True)
                if attempt == 2:
                    raise
                import time
                time.sleep(3)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually bookmark or commit — just run the agent and show what it picks.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Override config.bookmark_candidates_per_run",
    )
    args = parser.parse_args()

    config = load_config()
    require_env("ANTHROPIC_API_KEY")
    github_pat = require_env("GITHUB_PAT")

    now = dt.datetime.now().astimezone()
    now_iso = now.isoformat()

    max_candidates = args.max_candidates or config.get(
        "bookmark_candidates_per_run", 100
    )
    scroll_rounds = config.get("bookmark_feed_scroll_rounds", 8)

    # 1. Scrape feeds.
    print(f"[{now_iso}] bookmarker run — scraping feeds...", flush=True)
    candidates = fetch_feeds(
        chrome_user_data_dir=config["chrome_user_data_dir"],
        max_candidates=max_candidates,
        scroll_rounds=scroll_rounds,
        headless=True,
        verbose=False,
    )
    print(f"{len(candidates)} candidates scraped", flush=True)

    if not candidates:
        print("no candidates — exiting", flush=True)
        return

    # 2. Dedup against ingested + considered logs.
    print("loading dedup IDs from GitHub...", flush=True)
    dedup_ids = load_dedup_ids(github_pat, config["github_repo_url"])
    print(f"  {len(dedup_ids)} IDs already ingested or considered", flush=True)

    new_candidates = [c for c in candidates if c["tweet_id"] not in dedup_ids]
    print(f"  {len(new_candidates)} new candidates after dedup", flush=True)

    if not new_candidates:
        print("nothing new — exiting without agent session", flush=True)
        return

    # 3-4. Run agent session with custom-tool handling.
    client = anthropic.Anthropic()
    considered_records = stream_run(
        client=client,
        config=config,
        github_pat=github_pat,
        candidates=new_candidates,
        now_iso=now_iso,
        dry_run=args.dry_run,
    )

    # 5. Commit the considered log.
    if args.dry_run:
        bookmarked_count = sum(1 for r in considered_records if r.get("bookmarked"))
        print(
            f"\n[DRY RUN] would bookmark {bookmarked_count} tweets, "
            f"would log {len(considered_records)} considered",
            flush=True,
        )
        return

    print("\nappending considered log and pushing to KB...", flush=True)
    append_considered_log_and_push(
        github_pat=github_pat,
        repo_url=config["github_repo_url"],
        repo_branch=config["github_repo_branch"],
        new_records=considered_records,
    )

    bookmarked_count = sum(1 for r in considered_records if r.get("bookmarked"))
    print(
        f"\ndone. bookmarked {bookmarked_count} / considered {len(considered_records)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
