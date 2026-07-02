#!/usr/bin/env python3
"""
RUNTIME script for kb-podcast-curator.

Called every scheduled tick (by GitHub Actions cron, or manually).
Creates a fresh session against the pre-existing podcast agent and environment,
sends the kickoff message, streams events until the session goes idle or
terminates, and reports usage.

Reads all required IDs from environment variables:
    ANTHROPIC_API_KEY       — Anthropic API key
    PODCAST_AGENT_ID        — pre-created podcast agent ID (from podcast-setup.py)
    PODCAST_AGENT_VERSION   — podcast agent version (for reproducibility)
    PODCAST_ENV_ID          — pre-created podcast environment ID
    SEED_FILE_IDS           — comma-separated `filename:file_id` pairs (shared
                              with the blog agent — uploaded by setup.py)
    KB_REPO_URL             — e.g. https://github.com/<your-username>/<your-kb-repo>
    KB_REPO_TOKEN           — GitHub PAT or GITHUB_TOKEN with contents:write
    SLOT                    — "daily" (or "manual" for ad-hoc dispatches)
    SINGLE_URL              — optional: if set, skip discovery/ranking/synthesis
                              and analyze just this one episode URL (listen page,
                              transcript page, or YouTube URL). For manual
                              submissions from the reader app.

Usage:
    python podcast-run.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import anthropic

# Optional: load .env for local runs. In GitHub Actions, env comes from workflow
# secrets and python-dotenv isn't installed — skip silently in that case.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

AGENT_ID      = os.environ["PODCAST_AGENT_ID"]
AGENT_VERSION = os.environ.get("PODCAST_AGENT_VERSION")  # optional — pins reproducibility
ENV_ID        = os.environ["PODCAST_ENV_ID"]
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

SLOT          = os.environ.get("SLOT", "daily")
SEED_FILE_IDS_RAW = os.environ["SEED_FILE_IDS"]

# Optional: single-URL mode. When set, bypass discovery/ranking/synthesis and
# analyze just this one episode URL. Triggered by the reader app.
SINGLE_URL    = os.environ.get("SINGLE_URL", "").strip()


def _sanitize_url(raw: str) -> str:
    """
    Defense-in-depth URL validation. The reader's submission endpoint may
    already validate, but we also check here in case this script is invoked
    through another path (manual workflow_dispatch from the UI, etc.).
    Returns the URL on success, raises SystemExit on failure.
    """
    if not raw:
        return ""
    # Strip whitespace and control chars — prevents kickoff-message injection.
    cleaned = "".join(ch for ch in raw if ch >= " " and ch != "\x7f").strip()
    if cleaned != raw.strip():
        raise SystemExit("SINGLE_URL contains control characters; rejecting.")
    if len(cleaned) > 2000:
        raise SystemExit(f"SINGLE_URL too long ({len(cleaned)} chars).")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise SystemExit(f"SINGLE_URL must start with http:// or https://, got: {cleaned[:80]}")
    return cleaned


SINGLE_URL = _sanitize_url(SINGLE_URL)

# --------------------------------------------------------------------------
# Build the session resources list from SEED_FILE_IDS
# --------------------------------------------------------------------------

def parse_seed_file_ids(raw: str) -> list[dict]:
    """
    "subscriptions.md:file_abc,topic_taxonomy.md:file_xyz,..."
    →
    [{type: "file", file_id: "file_abc", mount_path: "/workspace/seed/subscriptions.md"}, ...]
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
    git_push_guidance = (
        f"GIT_PUSH_PAT={KB_REPO_TOKEN}\n\n"
        "If git push returns 503, fix the remote URL to bypass the CMA proxy:\n"
        f"    git remote set-url origin https://x-access-token:{KB_REPO_TOKEN}"
        f"@github.com/<your-username>/<your-kb-repo>.git\n"
        "Then retry the push."
    )

    if SINGLE_URL:
        # Manual single-URL submission from the reader app.
        # Skip discovery, ranking, and synthesis — just analyze this one URL
        # following the system prompt's podcast analysis format, then commit+push+stop.
        return (
            f"Manual single-URL submission from the reader app. "
            f"Current UTC time: {now}.\n\n"
            f"URL to analyze: {SINGLE_URL}\n\n"
            "Do ONLY the following, then stop:\n"
            "1. Load profile (interests_seed.md, topic_taxonomy.md) "
            "so you can assign topics and a relevance score correctly. You do NOT "
            "need to read your profile-podcast/ state (deltas, pinned_shows, etc.) "
            "for a one-off submission — skip that.\n"
            "2. Retrieve the transcript using your §4d fallback chain, treating "
            f"{SINGLE_URL} as the starting point:\n"
            "   - If it's already a transcript page, use it directly.\n"
            "   - If it's a show's listen page, look for a Transcript link and fetch that.\n"
            "   - If it's a YouTube URL, fetch auto-captions.\n"
            "   - If none of the above yield ≥5000 characters of prose, fall back to "
            "substantial show notes (≥2000 words) and set transcript_source: show-notes.\n"
            "   - If nothing usable, abort with a commit to the run log noting the "
            "failure; do NOT write a stub analysis.\n"
            "3. Write a full podcast analysis following your system prompt's format "
            "exactly — same frontmatter fields (source_type: podcast, show, host, "
            "guest, episode_title, episode_url, transcript_url, transcript_source, "
            "episode_duration, published_at, ingested_at, topics, relevance_score, "
            "user_score, slot) and same sections (TLDR, Guest Bio & Why They Matter, "
            "Episode Arc, What's New/Non-Obvious, Counterintuitive Claims, Steelman, "
            "Steelman Rebuttal, Forward-Looking Hypotheses, Technical Insights, Key "
            "Assumptions, Second-Order Implications, Direct Quotes, My Take, Talking "
            "Points). Skip any section that doesn't apply.\n"
            "4. Set slot: manual in the frontmatter. Leave user_score empty. Compute "
            "relevance_score as usual against the interest profile.\n"
            "5. Save as YYYY/MM/DD/podcast-<show-slug>-<3-word-slug>.md (today's PT date).\n"
            "6. Append one line to _system/meta/podcasts-ingested.jsonl (same schema as "
            "the scheduled pipeline). Update topic cross-refs if relevant. Commit with "
            "message 'podcast: manual analysis of <show> × <guest>' and push to main.\n"
            "7. Do NOT write a synthesis, run log, or daily README. The scheduled daily "
            "run handles those.\n"
            "8. Stop.\n\n"
            f"{git_push_guidance}"
        )

    return (
        f"Run the full podcast ingest pipeline for the {SLOT} slot. "
        f"Current UTC time: {now}. "
        "Follow your system prompt exactly: verify git push, load profile, "
        "drain feedback, passive learning, discovery (Tier 0 pinned shows + "
        "new-show hunt via web_search + host/guest inversion), transcript "
        "retrieval (official → YouTube → show-notes → skip), rank+cap at "
        "max 3 / score ≥8, analyze each winner with incremental commit+push, "
        "synthesis, daily README update, commit+push, stop.\n\n"
        f"{git_push_guidance}"
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

    mode_label = f"single-url={SINGLE_URL}" if SINGLE_URL else f"slot={SLOT}"
    print(f"[{datetime.now(timezone.utc).isoformat()}] creating session "
          f"({mode_label}, agent={AGENT_ID}, env={ENV_ID})...", flush=True)

    title_suffix = "manual-url" if SINGLE_URL else SLOT
    session = client.beta.sessions.create(
        agent=agent_ref,
        environment_id=ENV_ID,
        title=f"kb-podcast-curator / {title_suffix} / {datetime.now(timezone.utc).date()}",
        resources=resources,
    )
    print(f"session {session.id} status={session.status}", flush=True)

    # Stream-first: open the stream BEFORE sending the kickoff so we don't
    # miss the first few events.
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
