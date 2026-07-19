#!/usr/bin/env python3
"""
RUNTIME script for kb-blog-curator.

Called every scheduled tick (by GitHub Actions cron, or manually).

Cost-reduction architecture (2026-07): feed monitoring, dedupe, and ranking
run HERE — in plain Python plus one cheap claude-haiku-4-5 call — instead of
inside the Sonnet Managed Agents session. The session receives only the
pre-ranked candidates worth analyzing, which cuts its context (and the
cache-read token bill) by an order of magnitude. When there is nothing to
analyze and no feedback pending, non-morning runs skip the session entirely.

Flow:
  1. Read feed_map.json / blogs-ingested.jsonl / profile files from the local
     KB checkout (the workflow sparse-checks-out these paths).
  2. Concurrently fetch all cached feeds; filter to entries newer than the
     last ingest (hard 14-day cutoff); dedupe by canonical URL.
  3. Rank candidates with claude-haiku-4-5 against the owner's interest profile.
  4. Push the refreshed feed_map.json back via the GitHub contents API.
  5. If nothing selected and no feedback pending and slot != morning → exit
     without creating a session.
  6. Otherwise create the CMA session with the candidate list in the kickoff.
     Morning slot also runs the web-search new-source discovery hunt.
  7. Append per-run cost lines to _system/logs/costs.jsonl.

Reads all required IDs from environment variables:
    ANTHROPIC_API_KEY  — Anthropic API key
    AGENT_ID           — pre-created agent ID (from setup.py)
    AGENT_VERSION      — agent version (for reproducibility)
    ENV_ID             — pre-created environment ID
    SEED_FILE_IDS      — comma-separated `filename:file_id` pairs
    KB_REPO_URL        — e.g. https://github.com/<your-username>/<your-kb-repo>
    KB_REPO_TOKEN      — GitHub PAT or GITHUB_TOKEN with contents:write
    SLOT               — "morning" or "evening" (passed from the cron workflow)
    SINGLE_URL         — optional: if set, skip discovery/ranking/synthesis and
                         analyze just this one URL (for manual submissions from
                         the reader app)
    KB_CHECKOUT        — optional: path to the local KB checkout (defaults to
                         the parent of this script's directory)
    MAX_ANALYSES       — optional: cap on analyses per run (default 6)

Usage:
    python run-blog-ingest.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

# Optional: load .env for local runs. In GitHub Actions, env comes from workflow
# secrets and python-dotenv isn't installed — skip silently in that case.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_pipeline_lib as lib  # noqa: E402

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

# Committer identity for agent pushes. Use a GitHub-verified email so
# Vercel's committer-to-GitHub-user check passes on reader deploys.
GIT_COMMITTER_EMAIL = os.environ.get("GIT_COMMITTER_EMAIL", "you@example.com")
GIT_COMMITTER_NAME = os.environ.get("GIT_COMMITTER_NAME", "kb-curator")

SLOT          = os.environ.get("SLOT", "manual")
SEED_FILE_IDS_RAW = os.environ["SEED_FILE_IDS"]
KB_CHECKOUT   = Path(os.environ.get("KB_CHECKOUT") or Path(__file__).resolve().parent.parent)
MAX_ANALYSES  = int(os.environ.get("MAX_ANALYSES", "6"))
SCORE_FLOOR   = 8
SESSION_MODEL = "claude-sonnet-5"

# Discovery (web_search new-source hunt) only runs on the morning slot —
# once a day is enough for the KB's blog world to keep expanding.
DISCOVERY = SLOT in ("morning", "manual")

# Optional: single-URL mode. When set, bypass discovery/ranking/synthesis and
# analyze just this one URL. Triggered by the reader app's /api/analyze.
SINGLE_URL    = os.environ.get("SINGLE_URL", "").strip()


def _sanitize_url(raw: str) -> str:
    """
    Defense-in-depth URL validation. The reader's /api/analyze already
    validates, but we also check here in case this script is invoked
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
# Prefetch + rank (the work that used to burn session tokens)
# --------------------------------------------------------------------------

def _read_local(rel_path: str) -> str:
    try:
        return (KB_CHECKOUT / rel_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _load_feed_inbox(ingested: set, last_ingested) -> tuple[list[dict], set[str]]:
    """
    Read _system/profile/feed_inbox.jsonl (written by the Mac-side feed relay).
    Returns (fresh candidates, hosts the relay covered). Entries are trusted
    only if the relay ran within the last 36 hours.
    """
    import datetime as _dt

    raw = _read_local("_system/profile/feed_inbox.jsonl")
    if not raw.strip():
        return [], set()

    now = _dt.datetime.now(_dt.timezone.utc)
    relay_hosts: set[str] = set()
    fetched_at = None
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("_meta"):
            try:
                fetched_at = _dt.datetime.fromisoformat(row.get("fetched_at", ""))
            except ValueError:
                fetched_at = None
            relay_hosts = set(row.get("hosts_ok") or [])
            continue
        entries.append(row)

    if fetched_at is None or (now - fetched_at) > _dt.timedelta(hours=36):
        print("[prefetch] feed-relay inbox missing or stale (>36h) — ignoring", flush=True)
        return [], set()

    fresh = []
    for e in entries:
        if not e.get("url") or not e.get("published_at"):
            continue
        if e.get("canonical_url") in ingested:
            continue
        try:
            published = _dt.datetime.fromisoformat(e["published_at"])
        except ValueError:
            continue
        if published <= last_ingested:
            continue
        fresh.append({
            "url": e["url"],
            "canonical_url": e.get("canonical_url") or e["url"],
            "title": e.get("title", ""),
            "publication": e.get("publication", ""),
            "published_at": e["published_at"],
            "snippet": e.get("snippet", ""),
        })
    return fresh, relay_hosts


def feedback_pending() -> bool:
    """True if _system/profile/feedback.md has substantive content beyond the stub."""
    text = _read_local("_system/profile/feedback.md")
    if not text.strip():
        return False
    # Content after the stub's trailing "---" separator means the owner left feedback.
    tail = text.rsplit("---", 1)[-1] if "---" in text else text
    return bool(tail.strip())


def prefetch_and_rank(
    client: anthropic.Anthropic,
) -> tuple[list[dict], list[dict], int, list[dict], str]:
    """Returns (selected, all_scored, raw_candidate_count, failed_feeds, last_ingest_iso)."""
    feed_map_raw = _read_local("_system/profile/feed_map.json")
    feed_map = json.loads(feed_map_raw) if feed_map_raw.strip() else {}
    if not feed_map:
        print("[prefetch] feed_map.json empty/missing — no cached feeds to poll", flush=True)
        return [], [], 0, [], ""

    ingested, last_ingested = lib.load_ingested_urls(
        KB_CHECKOUT / "_system/meta/blogs-ingested.jsonl"
    )
    print(f"[prefetch] {len(feed_map)} cached feeds, {len(ingested)} URLs already "
          f"ingested, last ingest {last_ingested.isoformat()}", flush=True)

    candidates, updated_map, failed_feeds = lib.prefetch_feeds(feed_map, since=last_ingested)
    candidates = [c for c in candidates if c["canonical_url"] not in ingested]

    # Merge the Mac-side feed relay's inbox (fetched from a residential IP,
    # which Substack doesn't block). Hosts the relay covered recently don't
    # need the in-session fallback even if this runner couldn't fetch them.
    inbox_candidates, relay_hosts = _load_feed_inbox(ingested, last_ingested)
    if inbox_candidates:
        have = {c["canonical_url"] for c in candidates}
        merged_in = [c for c in inbox_candidates if c["canonical_url"] not in have]
        candidates.extend(merged_in)
        candidates.sort(key=lambda c: c["published_at"], reverse=True)
        print(f"[prefetch] merged {len(merged_in)} candidates from the feed-relay inbox",
              flush=True)
    if relay_hosts:
        before = len(failed_feeds)
        failed_feeds = [f for f in failed_feeds if f["host"] not in relay_hosts]
        print(f"[prefetch] relay covers {before - len(failed_feeds)} of {before} "
              f"unfetchable feeds", flush=True)

    print(f"[prefetch] {len(candidates)} new candidates after dedupe, "
          f"{len(failed_feeds)} feeds uncovered by runner+relay", flush=True)

    # Persist the refreshed cache metadata so failures/successes carry forward.
    if updated_map != feed_map:
        lib.put_repo_file(
            KB_REPO_URL, KB_REPO_TOKEN,
            "_system/profile/feed_map.json",
            json.dumps(updated_map, indent=2) + "\n",
            message=f"blog prefetch ({SLOT}): refresh feed_map",
        )

    last_ingest_iso = last_ingested.isoformat()
    if not candidates:
        return [], [], 0, failed_feeds, last_ingest_iso

    interests = _read_local("_system/profile/interests_seed.md")
    deltas = _read_local("_system/profile/deltas.md")
    selected, scored, rank_usage = lib.rank_candidates(
        client, candidates, interests, deltas,
        score_floor=SCORE_FLOOR, max_selected=MAX_ANALYSES,
    )
    lib.append_cost_log(
        KB_REPO_URL, KB_REPO_TOKEN,
        pipeline="blog-rank", slot=SLOT, model=lib.RANK_MODEL, usage=rank_usage,
        note=f"{len(candidates)} candidates → {len(selected)} selected",
    )
    print(f"[rank] {len(scored)} scored, {len(selected)} selected (floor {SCORE_FLOOR}, "
          f"cap {MAX_ANALYSES})", flush=True)
    for c in selected:
        print(f"  {c['score']} — [{c['publication']}] {c['title']}", flush=True)
    return selected, scored, len(candidates), failed_feeds, last_ingest_iso


# --------------------------------------------------------------------------
# Kickoff message
# --------------------------------------------------------------------------

def _git_push_guidance() -> str:
    return (
        "Before any git commit, set your identity so Vercel can match the author:\n"
        f"    git -C /workspace/kb config user.email '{GIT_COMMITTER_EMAIL}'\n"
        f"    git -C /workspace/kb config user.name '{GIT_COMMITTER_NAME}'\n\n"
        f"GIT_PUSH_PAT={KB_REPO_TOKEN}\n\n"
        "If git push returns 503, fix the remote URL to bypass the CMA proxy:\n"
        f"    git remote set-url origin https://x-access-token:{KB_REPO_TOKEN}"
        f"@github.com/<your-username>/<your-kb-repo>.git\n"
        "Then retry the push."
    )


def single_url_kickoff() -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"Manual single-URL submission from the reader app. "
        f"Current UTC time: {now}.\n\n"
        f"URL to analyze: {SINGLE_URL}\n\n"
        "Do ONLY the following, then stop:\n"
        "1. Load profile (interests_seed.md, topic_taxonomy.md) "
        "so you can assign topics and a relevance score correctly.\n"
        "2. Fetch the URL with web_fetch.\n"
        "3. Write a full blog analysis for this URL following your system "
        "prompt's analysis format exactly — same frontmatter fields "
        "(source_type, url, publication, author, title, published_at, "
        "ingested_at, topics, relevance_score, user_score, slot) and same "
        "sections (TLDR, What's New/Non-Obvious, Counterintuitive Claims, "
        "Steelman, Steelman Rebuttal, Technical Insights, Key Assumptions, "
        "Second-Order Implications, My Take, Talking Points).\n"
        "4. Set slot: manual in the frontmatter. Leave user_score empty. "
        "Compute relevance_score as usual against the interest profile.\n"
        "5. Save as YYYY/MM/DD/blog-<slug>.md (today's UTC date).\n"
        "6. Commit with message 'feat(blog): manual analysis of <slug>' "
        "and push to main. No synthesis, no run-log, no index updates — "
        "the scheduled runs handle those.\n"
        "7. Stop.\n\n"
        f"{_git_push_guidance()}"
    )


def scheduled_kickoff(
    selected: list[dict],
    scored: list[dict],
    raw_count: int,
    failed_feeds: list[dict],
    last_ingest_iso: str,
) -> str:
    now = datetime.now(timezone.utc).isoformat()

    if selected:
        cand_lines = []
        for i, c in enumerate(selected, 1):
            cand_lines.append(
                f"{i}. score {c['score']} — [{c['publication']}] {c['title']}\n"
                f"   url: {c['url']}\n"
                f"   published: {c['published_at']}\n"
                f"   rationale: {c['rationale']}"
            )
        cand_block = "\n".join(cand_lines)
    else:
        cand_block = "(none — no feed candidates cleared the ranking bar this run)"

    # The next few below-floor items feed the synthesis's "Considered but
    # Skipped" table without the agent re-doing any ranking work.
    skipped = [c for c in scored if c not in selected][:6]
    skipped_block = "\n".join(
        f"- score {c['score']} — [{c['publication']}] {c['title']} — {c['rationale']}"
        for c in skipped
    ) or "(none)"

    discovery_block = (
        "DISCOVERY=yes — this run, ALSO execute the new-source hunt (system prompt "
        "§4b-4c): max 5 web_search queries, record genuinely new publications in "
        "discovered_sources.md. Any discovery candidate that clearly scores ≥8 "
        "against the profile joins the analysis list (respect the 14-day recency "
        "rule and dedupe against blogs-ingested.jsonl)."
        if DISCOVERY else
        "DISCOVERY=no — do NOT run the new-source hunt this run. No web_search "
        "discovery queries. The morning run owns discovery."
    )

    # Feeds the orchestrator couldn't fetch (Substack's CDN blocks GitHub
    # Actions egress IPs). The morning session fetches these in-container —
    # CMA egress isn't blocked — so their content is at most a day late.
    if DISCOVERY and failed_feeds:
        feed_lines = "\n".join(
            f"- {f['host']}: {f['feed_url']}" for f in failed_feeds if f.get("feed_url")
        )
        fallback_block = (
            "\n\nUNFETCHABLE FEEDS — the orchestrator got HTTP errors on these feeds "
            "(its runner IP is blocked by their CDN; your container IP is not). "
            "Fetch EACH with `curl -sL <feed_url>` in your session, extract entries "
            f"with pubDate newer than {last_ingest_iso}, apply the 14-day cutoff, "
            "dedupe against blogs-ingested.jsonl, and add any entry that clearly "
            "scores ≥8 against the profile to your analysis list. Parse the XML "
            "economically (grep/awk for <title>/<link>/<pubDate>) — do not read "
            "whole feed bodies into context.\n"
            f"{feed_lines}"
        )
    else:
        fallback_block = ""

    return (
        f"Run the {SLOT} slot pipeline. Current UTC time: {now}.\n\n"
        "IMPORTANT — the orchestrator has already done feed monitoring for you. "
        f"It fetched all cached feeds, deduped against blogs-ingested.jsonl, applied "
        f"the 14-day recency cutoff, and ranked {raw_count} new candidates against "
        "the owner's interest profile. Therefore:\n"
        "- Do NOT fetch RSS feeds, probe feed URLs, or read subscriptions/url_sources "
        "to build a monitoring list.\n"
        "- Do NOT update _system/profile/feed_map.json — the orchestrator owns it now.\n"
        "- Do NOT re-rank the candidate pool.\n\n"
        f"{discovery_block}{fallback_block}\n\n"
        f"SELECTED CANDIDATES to analyze, in order (pre-ranked, score ≥{SCORE_FLOOR}):\n"
        f"{cand_block}\n\n"
        "Considered but not selected (for your synthesis's skipped table — do not "
        "analyze these):\n"
        f"{skipped_block}\n\n"
        "Pipeline for this run: verify git push, load profile, drain feedback, "
        "passive learning (user_score ratings + deletions), "
        + ("discovery hunt (per DISCOVERY flag above), " if DISCOVERY else "")
        + "then for each selected candidate: web_fetch the article, sanity-check it "
        "(skip with a note if paywalled, stale >14 days, or a pure retread of "
        "existing KB coverage), write the analysis, update topics, append to "
        "blogs-ingested.jsonl, commit+push — one candidate per commit, incremental. "
        "Then synthesis, daily README, run-log line, final commit+push, stop.\n\n"
        + _git_push_guidance()
    )


# --------------------------------------------------------------------------
# Session runner — stream-first, break gate on terminated OR idle-with-terminal-reason
# --------------------------------------------------------------------------

def run_session(client: anthropic.Anthropic, kickoff: str) -> tuple[str, dict, str]:
    """Create the CMA session, stream it to completion. Returns (session_id, usage, final_status)."""
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
        title=f"kb-blog-curator / {title_suffix} / {datetime.now(timezone.utc).date()}",
        resources=resources,
    )
    print(f"session {session.id} status={session.status}", flush=True)

    usage_total = lib.zero_usage()

    # Stream-first: open the stream BEFORE sending the kickoff so we don't
    # miss the first few events (see shared/managed-agents-client-patterns.md).
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": kickoff}],
            }],
        )

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
                    print("\n[idle — requires_action, unexpected]", file=sys.stderr)
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

    # Price from the model the session ACTUALLY ran (the resolved agent
    # snapshot) — a pinned AGENT_VERSION can differ from SESSION_MODEL.
    session_model = SESSION_MODEL
    try:
        m = getattr(final.agent, "model", None)
        resolved = m.get("id") if isinstance(m, dict) else (getattr(m, "id", None) or m)
        if isinstance(resolved, str) and resolved:
            session_model = resolved
    except Exception:  # noqa: BLE001
        pass

    print("\n" + "=" * 60)
    print(f"session {session.id} → final status: {final.status} (model={session_model})")
    print(f"usage totals: {usage_total}")
    print(f"estimated session cost: ${lib.estimate_cost_usd(usage_total, session_model)}")
    print("=" * 60)
    return session.id, usage_total, final.status, session_model


def main() -> int:
    client = anthropic.Anthropic()

    if SINGLE_URL:
        session_id, usage, final_status, run_model = run_session(client, single_url_kickoff())
        lib.append_cost_log(
            KB_REPO_URL, KB_REPO_TOKEN,
            pipeline="blog", slot="manual-url", model=run_model,
            usage=usage, session_id=session_id,
        )
        return 0 if final_status in ("idle", "terminated") else 1

    selected, scored, raw_count, failed_feeds, last_ingest_iso = prefetch_and_rank(client)
    fb = feedback_pending()

    # Skip gate: nothing selected, no feedback, and not the discovery slot →
    # the Sonnet session has literally nothing to do. Save the whole session.
    # (Unfetchable feeds don't block the skip — the morning run covers them.)
    if not selected and not fb and not DISCOVERY:
        print(f"[skip] no candidates ≥{SCORE_FLOOR}, no feedback pending, "
              f"DISCOVERY=no → skipping session entirely", flush=True)
        lib.append_cost_log(
            KB_REPO_URL, KB_REPO_TOKEN,
            pipeline="blog", slot=SLOT, model=SESSION_MODEL,
            usage=lib.zero_usage(), note="session skipped — nothing to do",
        )
        return 0

    session_id, usage, final_status, run_model = run_session(
        client, scheduled_kickoff(selected, scored, raw_count, failed_feeds, last_ingest_iso)
    )
    lib.append_cost_log(
        KB_REPO_URL, KB_REPO_TOKEN,
        pipeline="blog", slot=SLOT, model=run_model,
        usage=usage, session_id=session_id,
        note=f"{len(selected)} candidates, discovery={'yes' if DISCOVERY else 'no'}",
    )

    # Exit code: 0 on clean idle/terminated, 1 otherwise
    return 0 if final_status in ("idle", "terminated") else 1


if __name__ == "__main__":
    sys.exit(main())
