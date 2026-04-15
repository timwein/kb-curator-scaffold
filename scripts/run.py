#!/usr/bin/env python3
"""
RUNTIME script for kb-blog-curator.

Called every scheduled tick (by GitHub Actions cron, or manually).
Creates a fresh session against the pre-existing agent and environment,
sends the kickoff message, streams events until the session goes idle or
terminates, and reports usage.

Reads all required IDs from environment variables:
    ANTHROPIC_API_KEY  — Anthropic API key
    AGENT_ID           — pre-created agent ID (from setup.py)
    AGENT_VERSION      — agent version (for reproducibility)
    ENV_ID             — pre-created environment ID
    SEED_FILE_IDS      — comma-separated `filename:file_id` pairs
    KB_REPO_URL        — e.g. https://github.com/<your-username>/<your-kb-repo>
    KB_REPO_TOKEN      — GitHub PAT or GITHUB_TOKEN with contents:write
    SLOT               — "morning" or "evening" (passed from the cron workflow)

Usage:
    python run.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import anthropic

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

AGENT_ID      = os.environ["AGENT_ID"]
AGENT_VERSION = os.environ.get("AGENT_VERSION")  # optional — pins reproducibility
ENV_ID        = os.environ["ENV_ID"]
KB_REPO_URL   = os.environ["KB_REPO_URL"]

# Token fallback chain: explicit KB_REPO_TOKEN → local dev GITHUB_PAT → GH Actions built-in
KB_REPO_TOKEN = (
    os.environ.get("KB_REPO_TOKEN")
    or os.environ.get("GITHUB_PAT")
    or os.environ.get("GITHUB_TOKEN")
)
if not KB_REPO_TOKEN:
    raise SystemExit(
        "No GitHub token found. Set one of: KB_REPO_TOKEN, GITHUB_PAT, GITHUB_TOKEN "
        "(must have contents:write on the KB repo)."
    )

SLOT          = os.environ.get("SLOT", "manual")
SEED_FILE_IDS_RAW = os.environ["SEED_FILE_IDS"]

# --------------------------------------------------------------------------
# Build the session resources list from SEED_FILE_IDS
# --------------------------------------------------------------------------

def parse_seed_file_ids(raw: str) -> list[dict]:
    """
    "interests_seed.md:file_abc,topic_taxonomy.md:file_xyz,..."
    →
    [{type: "file", file_id: "file_abc", mount_path: "/workspace/seed/interests_seed.md"}, ...]
    """
    resources: list[dict] = []
    for pair in raw.split(","):
        name, _, fid = pair.strip().partition(":")
        if not name or not fid:
            continue
        resources.append({
            "type": "file",
            "file_id": fid,
            "mount_path": f"/workspace/seed/{name}",
        })
    return resources


# --------------------------------------------------------------------------
# Kickoff message
# --------------------------------------------------------------------------

def kickoff_text() -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"Run the full pipeline for the {SLOT} slot. "
        f"Current UTC time: {now}. "
        "Follow your system prompt exactly: verify git push, load profile, "
        "drain feedback, passive learning, discovery, rank+cap, analyze, "
        "topics, synthesis, index, commit+push, stop.\n\n"
        f"GIT_PUSH_PAT={KB_REPO_TOKEN}\n\n"
        "If git push returns 503, fix the remote URL to bypass the CMA proxy:\n"
        f"    git remote set-url origin https://x-access-token:{KB_REPO_TOKEN}"
        f"@github.com/<your-username>/<your-kb-repo>.git\n"
        "Then retry the push."
    )


# --------------------------------------------------------------------------
# Main — stream-first, break gate on terminated OR idle-with-terminal-reason
# --------------------------------------------------------------------------

def main() -> int:
    client = anthropic.Anthropic()

    resources: list[dict] = parse_seed_file_ids(SEED_FILE_IDS_RAW)
    resources.append({
        "type": "github_repository",
        "url": KB_REPO_URL,
        "authorization_token": KB_REPO_TOKEN,
        "mount_path": "/workspace/kb",
        "checkout": {"type": "branch", "name": "main"},
    })

    agent_ref: dict | str
    if AGENT_VERSION:
        agent_ref = {"type": "agent", "id": AGENT_ID, "version": int(AGENT_VERSION)}
    else:
        agent_ref = AGENT_ID  # string shorthand → latest version

    print(f"[{datetime.now(timezone.utc).isoformat()}] creating session "
          f"(slot={SLOT}, agent={AGENT_ID}, env={ENV_ID})...", flush=True)

    session = client.beta.sessions.create(
        agent=agent_ref,
        environment_id=ENV_ID,
        title=f"kb-blog-curator / {SLOT} / {datetime.now(timezone.utc).date()}",
        resources=resources,
    )
    print(f"session {session.id} status={session.status}", flush=True)

    # Stream-first: open the stream BEFORE sending the kickoff so we don't
    # miss the first few events (see shared/managed-agents-client-patterns.md).
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": kickoff_text()}],
            }],
        )

        usage_total = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        for event in stream:
            etype = getattr(event, "type", None)

            # Stream agent text deltas
            if etype == "agent.message":
                for block in getattr(event, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        sys.stdout.write(getattr(block, "text", ""))
                        sys.stdout.flush()

            # Track token usage
            elif etype == "span.model_request_end":
                mu = getattr(event, "model_usage", None)
                if mu:
                    for k in usage_total:
                        usage_total[k] += getattr(mu, k, 0) or 0

            # Errors surface as events, not exceptions
            elif etype == "session.error":
                print(f"\n[session.error] {event}", file=sys.stderr, flush=True)

            # Break gate — terminal conditions only
            elif etype == "session.status_terminated":
                print("\n[session.status_terminated]", flush=True)
                break
            elif etype == "session.status_idle":
                stop_reason = getattr(event, "stop_reason", None)
                reason_type = getattr(stop_reason, "type", None) if stop_reason else None
                if reason_type == "requires_action":
                    # Waiting on us for something (shouldn't happen — we have no
                    # custom tools and always_allow policy — but be defensive).
                    print(f"\n[idle — requires_action, unexpected]", file=sys.stderr)
                    continue
                print(f"\n[session.status_idle, stop_reason={reason_type}]", flush=True)
                break

    # Post-idle status-write race: give the server a beat before querying final state
    for _ in range(10):
        final = client.beta.sessions.retrieve(session.id)
        if final.status != "running":
            break
        time.sleep(0.2)
    else:
        final = client.beta.sessions.retrieve(session.id)

    print("\n" + "=" * 60)
    print(f"session {session.id} → final status: {final.status}")
    print(f"usage totals: {usage_total}")
    print("=" * 60)

    # Exit code: 0 on clean idle/terminated, 1 otherwise
    return 0 if final.status in ("idle", "terminated") else 1


if __name__ == "__main__":
    sys.exit(main())
