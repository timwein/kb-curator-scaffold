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
    python run_tweet_ingest.py
    python run_tweet_ingest.py --since 2026-01-01   # backfill mode
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import base64

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
        sys.exit(f"ERROR: {CONFIG_PATH} not found. Run setup_tweet_ingest.py first.")
    config = json.loads(CONFIG_PATH.read_text())

    placeholder = (
        not config.get("agent_id")
        or str(config.get("agent_id", "")).startswith("agent_REPLACE")
        or not config.get("environment_id")
        or str(config.get("environment_id", "")).startswith("env_REPLACE")
    )
    if placeholder:
        sys.exit("ERROR: config.json has placeholder IDs. Run setup_tweet_ingest.py first.")
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


def generate_run_log(
    now: dt.datetime,
    slot: str,
    bookmarks_fetched: int,
    already_ingested: int,
    new_bookmarks: list[dict],
    batches_count: int,
) -> str:
    """Generate markdown content for the tweet agent run-log."""
    tz_label = now.strftime("%Z") or "local"
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    new_count = len(new_bookmarks)

    lines = [
        f"# Tweet Ingest Run — {date_str} {time_str} {tz_label} ({slot})",
        "",
        f"**Started:** {now.isoformat()}",
        f"**Slot:** {slot}",
        f"**Bookmarks fetched:** {bookmarks_fetched}",
        f"**Already ingested:** {already_ingested}",
        f"**New tweets ingested:** {new_count}",
        f"**Batches:** {batches_count}",
        "",
        "---",
        "",
        "## Ingested This Run",
        "",
        "| # | Author | Snippet |",
        "|---|--------|---------|",
    ]
    for i, bm in enumerate(new_bookmarks, 1):
        author = bm.get("author", "unknown")
        text = bm.get("text", "").replace("\n", " ").strip()
        snippet = (text[:97] + "...") if len(text) > 100 else text
        snippet = snippet.replace("|", "\\|")
        lines.append(f"| {i} | {author} | {snippet} |")
    lines.append("")
    return "\n".join(lines)


def update_readme_tweet_section(
    date: dt.date,
    slot: str,
    now: dt.datetime,
    github_pat: str,
    repo_url: str,
) -> None:
    """Patch the ## Tweet Agent section in the daily README after ingestion.

    Fetches the current directory listing to build a table of all tweet
    analysis files committed today, then replaces the Tweet Agent section
    (which starts as '*(not yet run)*') with the populated table.
    """
    import urllib.request
    import urllib.error
    import re

    repo_path = repo_url.removeprefix("https://github.com/").rstrip("/")
    date_dir = date.strftime("%Y/%m/%d")
    headers = {
        "Authorization": f"token {github_pat}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "tweet-ingestion-agent/1.0",
    }

    # 1. List today's directory to find tweet analysis files.
    dir_url = f"https://api.github.com/repos/{repo_path}/contents/{date_dir}"
    try:
        with urllib.request.urlopen(urllib.request.Request(dir_url, headers=headers)) as resp:
            entries = json.loads(resp.read())
    except Exception as e:
        print(f"[readme] could not list {date_dir}: {e}", flush=True)
        return

    tweet_files = sorted(
        e["name"] for e in entries
        if isinstance(e, dict)
        and re.match(r"^\d+[-]", e.get("name", ""))
        and e.get("name", "").endswith(".md")
    )
    if not tweet_files:
        print("[readme] no tweet files found, skipping README update", flush=True)
        return

    # 2. Build the replacement section.
    def filename_to_row(i: int, fname: str) -> str:
        stem = fname[:-3]  # strip .md
        parts = stem.split("-", 2)
        handle = f"@{parts[1]}" if len(parts) >= 2 else "unknown"
        display = parts[2].replace("-", " ").capitalize() if len(parts) >= 3 else stem
        return f"| {i} | [{display}]({fname}) | {handle} |"

    time_str = now.strftime("%-I:%M %p")
    table_rows = [filename_to_row(i + 1, f) for i, f in enumerate(tweet_files)]

    new_section = "\n".join([
        "## Tweet Agent",
        "",
        f"### {slot.capitalize()} ({time_str})",
        f"**→ [Run Log](run-log-tweet-{slot}.md)** — {len(tweet_files)} tweets",
        "",
        "| # | Analysis | Author |",
        "|---|----------|--------|",
    ] + table_rows)

    # 3. Fetch the current README (JSON API gives sha + base64 content in one shot).
    readme_url = f"https://api.github.com/repos/{repo_path}/contents/{date_dir}/README.md"
    try:
        with urllib.request.urlopen(urllib.request.Request(readme_url, headers=headers)) as resp:
            readme_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[readme] could not fetch README: {e}", flush=True)
        return

    current_sha = readme_data["sha"]
    # GitHub wraps base64 content with newlines; strip before decoding.
    current_content = base64.b64decode(
        readme_data["content"].replace("\n", "")
    ).decode("utf-8")

    # 4. Replace the Tweet Agent section (up to next ## heading, --- divider, or EOF).
    updated = re.sub(
        r"## Tweet Agent\n.*?(?=\n## |\n---|\Z)",
        new_section,
        current_content,
        flags=re.DOTALL,
    )

    if updated == current_content:
        print("[readme] Tweet Agent section unchanged — skipping push", flush=True)
        return

    # 5. Commit the patched README.
    payload = {
        "message": f"tweet agent {slot}: update README ({len(tweet_files)} tweets)",
        "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
        "sha": current_sha,
    }
    put_req = urllib.request.Request(
        readme_url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(put_req) as resp:
            result = json.loads(resp.read())
            html_url = result.get("content", {}).get("html_url", readme_url)
            print(f"[readme] README updated → {html_url}", flush=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[readme] push failed: {e} — {body}", flush=True)


def push_run_log_to_github(
    content: str,
    date: dt.date,
    slot: str,
    github_pat: str,
    repo_url: str,
) -> None:
    """Commit the run-log file to GitHub via the Contents API."""
    import urllib.request
    import urllib.error

    repo_path = repo_url.removeprefix("https://github.com/").rstrip("/")
    file_path = f"{date.strftime('%Y/%m/%d')}/run-log-tweet-{slot}.md"
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{file_path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    # Check if file already exists to get its SHA (required for updates).
    sha: str | None = None
    get_req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {github_pat}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "tweet-ingestion-agent/1.0",
        },
    )
    try:
        with urllib.request.urlopen(get_req) as resp:
            sha = json.loads(resp.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    payload: dict = {
        "message": f"run-log: tweet agent {slot} {date.isoformat()}",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    put_req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"token {github_pat}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "tweet-ingestion-agent/1.0",
        },
    )
    with urllib.request.urlopen(put_req) as resp:
        result = json.loads(resp.read())
        html_url = result.get("content", {}).get("html_url", file_path)
        print(f"run-log pushed → {html_url}", flush=True)


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

    print("writing run-log...", flush=True)
    run_log = generate_run_log(
        now=now,
        slot=slot,
        bookmarks_fetched=len(bookmarks),
        already_ingested=len(ingested_ids),
        new_bookmarks=new_bookmarks,
        batches_count=total_batches,
    )
    push_run_log_to_github(
        content=run_log,
        date=now.date(),
        slot=slot,
        github_pat=github_pat,
        repo_url=config["github_repo_url"],
    )

    print("updating README...", flush=True)
    update_readme_tweet_section(
        date=now.date(),
        slot=slot,
        now=now,
        github_pat=github_pat,
        repo_url=config["github_repo_url"],
    )


if __name__ == "__main__":
    main()
