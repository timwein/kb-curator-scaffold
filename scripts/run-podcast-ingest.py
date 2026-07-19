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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import kb_pipeline_lib as lib
except ImportError:  # source-repo copy run outside the KB checkout
    lib = None

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

# Committer identity for agent pushes. Use a GitHub-verified email so
# Vercel's committer-to-GitHub-user check passes on reader deploys.
GIT_COMMITTER_EMAIL = os.environ.get("GIT_COMMITTER_EMAIL", "you@example.com")
GIT_COMMITTER_NAME = os.environ.get("GIT_COMMITTER_NAME", "kb-curator")

SLOT          = os.environ.get("SLOT", "daily")
SEED_FILE_IDS_RAW = os.environ["SEED_FILE_IDS"]
SESSION_MODEL = "claude-sonnet-5"

# Cost control: the web_search new-show hunt (host/guest inversion, sweeps —
# ~10-15 searches worth of session context) runs only twice a week. Tier 0
# pinned-show monitoring still happens every day.
_PT_WEEKDAY = datetime.now(timezone.utc).astimezone().weekday()  # runner is UTC; close enough for a day-gate
try:
    from zoneinfo import ZoneInfo
    _PT_WEEKDAY = datetime.now(ZoneInfo("America/Los_Angeles")).weekday()
except Exception:  # noqa: BLE001
    pass
DISCOVERY = SLOT == "manual" or _PT_WEEKDAY in (0, 3)  # Monday, Thursday PT

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
# Orchestrator prefetch + early-exit gate (scheduled runs only)
#
# The agent's §0.5 early-exit gate ran INSIDE the session, so a "nothing new
# today" verdict still cost a full session setup (~$4-5). Worse, it compared
# feed state against podcasts-ingested.jsonl, which only records episodes that
# PASSED the quality bar — so a below-threshold episode looked "new" on every
# subsequent run and the gate almost never fired.
#
# This gate runs here, in plain Python, before any session exists:
#   - Resolves each known show to an RSS/Atom feed (autodiscovered once,
#     cached in _system/meta/podcast_feeds.json — runner-owned).
#   - Fetches feeds concurrently and collects episodes from the last 14 days.
#   - Dedupes against podcasts-ingested.jsonl AND podcast_episodes_seen.jsonl
#     (runner-owned ledger of every episode ever handed to a session, whether
#     or not it was ingested — this is what fixes the rejected-episode leak).
#   - Skips session creation entirely when there are no new episodes, no
#     substantive feedback, no unresolved feeds, and a full session ran within
#     the last MAX_FULL_RUN_GAP_DAYS (safety valve so feedless shows and
#     discovery still get periodic in-session attention).
# --------------------------------------------------------------------------

import json
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin

KB_CHECKOUT = Path(os.environ.get("KB_CHECKOUT") or Path(__file__).resolve().parent.parent)

PODCAST_FEEDS_PATH = "_system/meta/podcast_feeds.json"
PODCAST_SEEN_PATH = "_system/meta/podcast_episodes_seen.jsonl"
PODCAST_RUNLOG_PATH = "_system/logs/podcast.jsonl"
FEED_CUTOFF_DAYS = 14
MAX_FULL_RUN_GAP_DAYS = 3
MAX_AUTODISCOVER_FAILURES = 3  # after this many, a host stops blocking the skip path
_FEED_PROBE_PATHS = ("/feed", "/rss", "/feed/podcast", "/podcast/feed", "/rss.xml", "/feed.xml")


def _read_kb_text(rel_path: str) -> str | None:
    p = KB_CHECKOUT / rel_path
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _pinned_hosts() -> list[str]:
    text = _read_kb_text("_system/profile-podcast/pinned_shows.md") or ""
    hosts = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            hosts.append(line)
    return hosts


def _show_index_urls() -> dict:
    """host → index_url from the agent-owned show_feed_map.json."""
    text = _read_kb_text("_system/profile-podcast/show_feed_map.json") or "{}"
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return {h: (m or {}).get("index_url") for h, m in raw.items() if isinstance(m, dict)}


def _autodiscover_feed(host: str, index_url: str | None) -> str | None:
    """Find a working RSS/Atom feed for a show. Returns feed URL or None."""
    candidates: list[str] = []
    if index_url:
        try:
            html = lib._http_get(index_url).decode("utf-8", errors="replace")
            for m in re.finditer(
                r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', html, re.I
            ):
                href = re.search(r'href=["\']([^"\']+)["\']', m.group(0))
                if href:
                    candidates.append(urljoin(index_url, href.group(1)))
        except Exception:  # noqa: BLE001
            pass
    base = f"https://{host.split('/', 1)[0]}"
    path_prefix = "/" + host.split("/", 1)[1] if "/" in host else ""
    for probe in _FEED_PROBE_PATHS:
        candidates.append(base + path_prefix + probe)
        if path_prefix:
            candidates.append(base + probe)
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            if lib.parse_feed(lib._http_get(url)):
                return url
        except Exception:  # noqa: BLE001
            continue
    return None


def _feedback_pending() -> bool:
    """True if feedback.md has substantive content beyond the stub template."""
    text = _read_kb_text("_system/profile-podcast/feedback.md")
    if text is None:
        return False
    tail = text.rsplit("---", 1)[-1] if "---" in text else ""
    return bool(tail.strip())


def _days_since_last_full_run() -> float:
    """Days since the last podcast.jsonl entry that wasn't a skip."""
    text = _read_kb_text(PODCAST_RUNLOG_PATH) or ""
    latest = None
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("status", "").startswith("skipped"):
            continue
        if row.get("date"):
            latest = row["date"]
    if not latest:
        return float("inf")
    try:
        last = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - last).total_seconds() / 86400


def _load_seen_urls() -> set[str]:
    urls: set[str] = set()
    text = _read_kb_text(PODCAST_SEEN_PATH) or ""
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("url"):
            urls.add(lib.canonical_url(row["url"]))
    return urls


def run_prefetch_gate() -> dict:
    """
    Returns {skip, candidates, covered_hosts, unresolved_hosts, feeds, feeds_changed}.
    Never raises — any unexpected failure returns skip=False so the session
    runs and the agent handles things itself (the pre-gate behavior).
    """
    result = {
        "skip": False, "candidates": [], "covered_hosts": [],
        "unresolved_hosts": [], "feeds": {}, "feeds_changed": False,
    }
    try:
        feeds_raw = lib.get_repo_file(KB_REPO_URL, KB_REPO_TOKEN, PODCAST_FEEDS_PATH)
        feeds: dict = json.loads(feeds_raw) if feeds_raw else {}
        index_urls = _show_index_urls()
        hosts = sorted(set(_pinned_hosts()) | set(index_urls))
        changed = False

        for host in hosts:
            meta = feeds.setdefault(host, {})
            if meta.get("feed_url") or meta.get("feed_type") == "html_fallback":
                continue
            feed_url = _autodiscover_feed(host, index_urls.get(host))
            if feed_url:
                meta["feed_url"] = feed_url
                meta["discovered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                meta.pop("autodiscover_failures", None)
                print(f"[gate] {host}: feed autodiscovered → {feed_url}", flush=True)
            else:
                meta["autodiscover_failures"] = meta.get("autodiscover_failures", 0) + 1
                if meta["autodiscover_failures"] >= MAX_AUTODISCOVER_FAILURES:
                    meta["feed_type"] = "html_fallback"
                    print(f"[gate] {host}: no feed found after "
                          f"{meta['autodiscover_failures']} tries — agent-checked from now on", flush=True)
            changed = True

        ingested, _ = lib.load_ingested_urls(KB_CHECKOUT / "_system/meta/podcasts-ingested.jsonl")
        # podcasts-ingested.jsonl uses "episode_url", not "url" — load those too.
        for line in (_read_kb_text("_system/meta/podcasts-ingested.jsonl") or "").splitlines():
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if row.get("episode_url"):
                ingested.add(lib.canonical_url(row["episode_url"]))
        seen = _load_seen_urls()

        since = datetime.now(timezone.utc) - timedelta(days=FEED_CUTOFF_DAYS)
        entries, updated_feeds, failed = lib.prefetch_feeds(feeds, since, cutoff_days=FEED_CUTOFF_DAYS)
        changed = changed or (json.dumps(updated_feeds, sort_keys=True) != json.dumps(feeds, sort_keys=True))
        feeds = updated_feeds

        fresh = [
            c for c in entries
            if c["canonical_url"] not in ingested and c["canonical_url"] not in seen
        ]
        covered = [h for h, m in feeds.items() if m.get("feed_url") and not any(
            f["host"] == h for f in failed)]
        unresolved = sorted(
            {f["host"] for f in failed}
            | {h for h, m in feeds.items()
               if not m.get("feed_url") and m.get("feed_type") != "html_fallback"}
        )
        agent_checked = sorted(
            h for h, m in feeds.items() if m.get("feed_type") == "html_fallback")

        gap_days = _days_since_last_full_run()
        feedback = _feedback_pending()
        skip = (
            not fresh
            and not unresolved
            and not feedback
            and gap_days < MAX_FULL_RUN_GAP_DAYS
        )
        print(f"[gate] hosts={len(hosts)} covered={len(covered)} unresolved={len(unresolved)} "
              f"agent-checked={len(agent_checked)} new-episodes={len(fresh)} "
              f"feedback={'yes' if feedback else 'no'} days-since-full-run={gap_days:.1f} "
              f"→ {'SKIP' if skip else 'RUN'}", flush=True)

        result.update({
            "skip": skip, "candidates": fresh, "covered_hosts": covered,
            "unresolved_hosts": unresolved + agent_checked,
            "feeds": feeds, "feeds_changed": changed,
        })
    except Exception as e:  # noqa: BLE001 — gate failure must never block the pipeline
        print(f"[gate] prefetch gate failed ({e}) — falling back to a full session", flush=True)
    return result


def persist_gate_state(gate: dict, skipped: bool) -> None:
    """Write the feed cache, seen-ledger, and (on skip) the run-log line."""
    if gate["feeds_changed"]:
        lib.put_repo_file(
            KB_REPO_URL, KB_REPO_TOKEN, PODCAST_FEEDS_PATH,
            json.dumps(gate["feeds"], indent=2, sort_keys=True) + "\n",
            "podcast gate: update feed cache",
        )
    if skipped:
        date_pt = datetime.now(timezone.utc).astimezone().date().isoformat()
        try:
            from zoneinfo import ZoneInfo
            date_pt = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        except Exception:  # noqa: BLE001
            pass
        line = json.dumps({
            "date": date_pt, "slot": SLOT, "status": "skipped_no_new_content",
            "gate": "orchestrator", "hosts_checked": len(gate["covered_hosts"]),
        })
        existing = lib.get_repo_file(KB_REPO_URL, KB_REPO_TOKEN, PODCAST_RUNLOG_PATH) or ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        lib.put_repo_file(
            KB_REPO_URL, KB_REPO_TOKEN, PODCAST_RUNLOG_PATH,
            existing + line + "\n",
            "podcast ingest (daily): skip — no new content (orchestrator gate)",
        )


def record_seen_candidates(candidates: list[dict]) -> None:
    """Append surfaced candidates to the seen-ledger after a clean session."""
    if not candidates:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = lib.get_repo_file(KB_REPO_URL, KB_REPO_TOKEN, PODCAST_SEEN_PATH) or ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    for c in candidates:
        existing += json.dumps({
            "url": c["url"], "title": c["title"],
            "publication": c["publication"], "surfaced_at": now,
        }) + "\n"
    lib.put_repo_file(
        KB_REPO_URL, KB_REPO_TOKEN, PODCAST_SEEN_PATH, existing,
        f"podcast gate: mark {len(candidates)} episodes seen",
    )


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

def kickoff_text(gate: dict | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat()
    git_push_guidance = (
        "Before any git commit, set your identity so Vercel can match the author:\n"
        f"    git -C /workspace/kb config user.email '{GIT_COMMITTER_EMAIL}'\n"
        f"    git -C /workspace/kb config user.name '{GIT_COMMITTER_NAME}'\n\n"
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

    if gate and (gate["covered_hosts"] or gate["candidates"]):
        cand_json = json.dumps(
            [{k: c[k] for k in ("url", "title", "publication", "published_at", "snippet")}
             for c in gate["candidates"]],
            indent=2, ensure_ascii=False,
        )
        prefetch_block = (
            "FEED MONITORING IS ALREADY DONE. The orchestrator fetched every known "
            "show feed before creating this session and pre-deduped against both "
            "podcasts-ingested.jsonl and the seen-episodes ledger. Do NOT re-fetch "
            "index pages or feeds for these hosts, and SKIP your §0.5 early-exit "
            "gate — the orchestrator already ran it.\n\n"
            f"Hosts checked and current: {', '.join(gate['covered_hosts']) or '(none)'}\n"
            f"Hosts the orchestrator could NOT check — verify these in-session per §4a: "
            f"{', '.join(gate['unresolved_hosts']) or '(none)'}\n\n"
            "New candidate episodes (treat this as your §4a monitoring output — "
            "score them per §5, cap at max 3 / score ≥8):\n\n"
            f"<candidates>\n{cand_json}\n</candidates>\n\n"
        )
    else:
        prefetch_block = (
            "The orchestrator's feed prefetch was unavailable this run — do your "
            "own §0.5 early-exit gate and §4a monitoring as usual.\n\n"
        )

    return (
        f"Run the full podcast ingest pipeline for the {SLOT} slot. "
        f"Current UTC time: {now}.\n\n"
        + prefetch_block
        + (
            "DISCOVERY=yes — this run includes the new-show hunt "
            "(web_search + host/guest inversion + sweeps, per your system prompt).\n\n"
            if DISCOVERY else
            "DISCOVERY=no — SKIP the new-show hunt entirely this run: no "
            "web_search discovery queries, no host/guest inversion, no "
            "'what's new' sweeps. The twice-weekly discovery run (Mon/Thu) "
            "owns new-show hunting.\n\n"
        )
        + "Follow your system prompt: verify git push, load "
        "profile, drain feedback, passive learning, discovery per the "
        "DISCOVERY flag above, transcript "
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

    gate: dict | None = None
    if not SINGLE_URL and lib is not None:
        gate = run_prefetch_gate()
        if gate["skip"]:
            persist_gate_state(gate, skipped=True)
            print("nothing new — exiting without a session (saved a full session cost)",
                  flush=True)
            return 0
        persist_gate_state(gate, skipped=False)

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
                "content": [{"type": "text", "text": kickoff_text(gate)}],
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
    if lib is not None:
        print(f"estimated session cost: ${lib.estimate_cost_usd(usage_total, session_model)}")
    print("=" * 60)

    # Mark surfaced candidates as seen so rejected episodes don't resurface —
    # but only after a clean session end, so a crashed session gets a retry.
    if gate and final.status in ("idle", "terminated"):
        try:
            record_seen_candidates(gate["candidates"])
        except Exception as e:  # noqa: BLE001 — bookkeeping must not fail the run
            print(f"[gate] seen-ledger append failed (non-fatal): {e}", flush=True)

    if lib is not None:
        lib.append_cost_log(
            KB_REPO_URL, KB_REPO_TOKEN,
            pipeline="podcast",
            slot="manual-url" if SINGLE_URL else SLOT,
            model=session_model,
            usage=usage_total,
            session_id=session.id,
            note=f"discovery={'yes' if DISCOVERY else 'no'}",
        )

    # Exit code: 0 on clean idle/terminated, 1 otherwise
    return 0 if final.status in ("idle", "terminated") else 1


if __name__ == "__main__":
    sys.exit(main())
