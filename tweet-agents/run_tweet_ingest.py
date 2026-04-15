"""
RUNTIME ORCHESTRATOR — run 3x/day via cron/launchd.

Flow:
  1. Fetch current X bookmarks via Playwright (headless Chromium, persistent profile)
  2. Dedupe against meta/ingested.jsonl fetched from GitHub
  3. Split new bookmarks into batches (default 20 per batch)
  4. For each batch: create a CMA session, send the batch, stream output, archive
  5. Last batch writes the synthesis + updates index.md

Each batch gets its own session and commit — work is preserved incrementally even
if a later batch fails. The repo is the synthesis destination; read results on any device.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export GITHUB_PAT=github_pat_...
    python run.py
    python run.py --since 2026-01-01   # backfill mode
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import anthropic

from lib.fetcher import enrich_full_text, fetch_bookmarks
from lib.prompts import build_kickoff_message


CONFIG_PATH = Path(__file__).parent / "config.json"
BATCH_SIZE = 20  # tweets per agent session


def current_slot(now: dt.datetime) -> str:
    """Return morning/midday/evening based on local hour."""
    h = now.hour
    if h < 11:
        return "morning"
    if h < 17:
        return "midday"
    return "evening"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"ERROR: {CONFIG_PATH} not found. Run setup.py first.")
    config = json.loads(CONFIG_PATH.read_text())

    placeholder = (
        not config.get("agent_id")
        or str(config.get("agent_id", "")).startswith("agent_REPLACE")
        or not config.get("environment_id")
        or str(config.get("environment_id", "")).startswith("env_REPLACE")
    )
    if placeholder:
        sys.exit("ERROR: config.json has placeholder IDs. Run setup.py first.")
    return config


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"ERROR: {name} is not set in the environment.")
    return val


def _handle_stream_events(stream) -> None:
    """Consume a sessions.events stream, printing agent output until done."""
    for event in stream:
        etype = getattr(event, "type", None)

        if etype == "agent.message":
            for block in getattr(event, "content", None) or []:
                if getattr(block, "type", None) == "text":
                    print(block.text, end="", flush=True)
            print()

        elif etype == "agent.tool_use":
            print(f"  [tool: {event.name}]", flush=True)

        elif etype == "agent.thinking":
            print("  [thinking...]", flush=True)

        elif etype == "session.error":
            print(f"\n[SESSION ERROR] {event}", file=sys.stderr, flush=True)
            return

        elif etype == "session.status_terminated":
            print("\n[session terminated]", flush=True)
            return

        elif etype == "session.status_idle":
            stop_reason = getattr(event, "stop_reason", None)
            stop_type = getattr(stop_reason, "type", None) if stop_reason else None
            if stop_type == "requires_action":
                print(
                    "\n[unexpected requires_action — no custom tools declared; breaking]",
                    flush=True,
                )
                return
            print(f"\n[session idle — {stop_type}]", flush=True)
            return


def fetch_ingested_ids(github_pat: str, repo_url: str) -> set[str]:
    """Fetch already-ingested tweet IDs from meta/ingested.jsonl on GitHub.

    Returns an empty set if the file doesn't exist yet.
    """
    import urllib.request, urllib.error

    # Convert https://github.com/owner/repo → owner/repo
    repo_path = repo_url.removeprefix("https://github.com/").rstrip("/")
    api_url = f"https://api.github.com/repos/{repo_path}/contents/meta/ingested.jsonl"
    req = urllib.request.Request(
        api_url,
        headers={"Authorization": f"token {github_pat}", "Accept": "application/vnd.github.raw"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return set()
        raise

    ids: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line)["tweet_id"])
        except (json.JSONDecodeError, KeyError):
            pass
    return ids


def run_batch(
    client: anthropic.Anthropic,
    config: dict,
    github_pat: str,
    batch: list[dict],
    batch_index: int,
    total_batches: int,
    slot: str,
    now_iso: str,
) -> None:
    """Create one CMA session for a batch of bookmarks and wait for it to finish."""
    date_str = now_iso[:10]
    print(
        f"\n[batch {batch_index + 1}/{total_batches}] {len(batch)} tweets — "
        f"creating session...",
        flush=True,
    )
    session = client.beta.sessions.create(
        agent=config["agent_id"],
        environment_id=config["environment_id"],
        title=f"tweet-kb {slot} {date_str} b{batch_index + 1}of{total_batches}",
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
    print(f"session: {session.id}", flush=True)

    kickoff = build_kickoff_message(
        items=batch,
        slot=slot,
        now_iso=now_iso,
        batch_index=batch_index,
        total_batches=total_batches,
        github_pat=github_pat,
    )

    print("\n--- agent output ---\n", flush=True)
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
            _handle_stream_events(stream)
    finally:
        try:
            client.beta.sessions.archive(session_id=session.id)
        except Exception as e:
            print(f"(archive deferred: {e})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only ingest bookmarks with a tweet date >= this date (backfill mode). "
             "Automatically raises the scrape cap to 2000.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Tweets per agent session (default: {BATCH_SIZE}).",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Skip scraping. Send one synthetic tweet to test agent formatting.",
    )
    args = parser.parse_args()

    config = load_config()
    require_env("ANTHROPIC_API_KEY")
    github_pat = require_env("GITHUB_PAT")

    now = dt.datetime.now().astimezone()
    slot = current_slot(now)

    if args.test:
        bookmarks = [
            {
                "tweet_id": "TEST_" + now.strftime("%Y%m%d%H%M%S"),
                "author": "@sama",
                "url": "https://x.com/sama/status/1909123456789",
                "tweet_datetime": now.isoformat(),
                "text": "The thing that keeps me up at night isn't that AI will be too powerful — it's that we'll fumble the transition. The hard part isn't the technology. It's the coordination.",
                "media_alt": None,
                "external_url": None,
                "is_thread": False,
            }
        ]
        print(f"[test mode] using 1 synthetic tweet, skipping scrape", flush=True)
    else:
        max_items = config.get("max_bookmarks_per_run", 100)
        since_iso = args.since or None
        if since_iso:
            max_items = 2000
            print(f"[backfill mode] since={since_iso}, scrape cap={max_items}", flush=True)

        print(f"[{now.isoformat()}] {slot} run — fetching bookmarks...", flush=True)
        bookmarks = fetch_bookmarks(
            chrome_user_data_dir=config["chrome_user_data_dir"],
            max_items=max_items,
            headless=True,
            since_iso=since_iso,
            verbose=True,
        )
        print(f"fetched {len(bookmarks)} bookmarks", flush=True)

    if not bookmarks:
        print("No bookmarks fetched. Check Chrome profile (run: python -m lib.fetcher --diagnose).")
        return

    # Dedupe against what's already in the repo.
    print("checking ingested.jsonl on GitHub...", flush=True)
    ingested_ids = fetch_ingested_ids(github_pat, config["github_repo_url"])
    new_bookmarks = [b for b in bookmarks if b["tweet_id"] not in ingested_ids]
    print(
        f"{len(new_bookmarks)} new (of {len(bookmarks)} fetched, "
        f"{len(ingested_ids)} already ingested)",
        flush=True,
    )

    if not new_bookmarks:
        print("Nothing new to ingest.", flush=True)
        return

    # Enrich truncated text by visiting each new tweet's permalink. X's
    # bookmarks page collapses long tweets behind "Show more"; the detail
    # page renders the full body. Only runs in non-test mode since test
    # tweets are synthetic.
    if not args.test:
        print(
            f"enriching full text for {len(new_bookmarks)} new tweet(s)...",
            flush=True,
        )
        enrich_full_text(
            bookmarks=new_bookmarks,
            chrome_user_data_dir=config["chrome_user_data_dir"],
            headless=True,
        )

    # Split into batches.
    batch_size = args.batch_size
    batches = [new_bookmarks[i:i + batch_size] for i in range(0, len(new_bookmarks), batch_size)]
    total_batches = len(batches)
    print(
        f"{total_batches} batch(es) of up to {batch_size} — starting...",
        flush=True,
    )

    client = anthropic.Anthropic()
    now_iso = now.isoformat()

    for batch_index, batch in enumerate(batches):
        run_batch(
            client=client,
            config=config,
            github_pat=github_pat,
            batch=batch,
            batch_index=batch_index,
            total_batches=total_batches,
            slot=slot,
            now_iso=now_iso,
        )

    print("\nall batches done.", flush=True)


if __name__ == "__main__":
    main()
