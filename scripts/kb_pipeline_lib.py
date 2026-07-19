"""
Shared library for the KB ingestion pipelines (blog + podcast runners).

Cost-reduction architecture (2026-07): mechanical work that used to run
*inside* the Managed Agents session — feed fetching, dedupe, ranking —
now runs here in plain Python + one cheap Haiku call. The Sonnet session
only receives the handful of candidates worth analyzing, which cuts the
session's context (and its cache-read token bill) by an order of magnitude.

Provides:
  - canonical_url()        — tracking-param stripping, matches the agent's rules
  - prefetch_feeds()       — concurrent RSS/Atom fetch across the cached feed map
  - load_ingested_urls()   — dedupe set from _system/meta/blogs-ingested.jsonl
  - rank_candidates()      — claude-haiku-4-5 scoring against the owner's profile
  - estimate_cost_usd()    — $ estimate from a usage dict
  - append_cost_log()      — append a JSONL line to _system/logs/costs.jsonl
                             via the GitHub contents API (no local git needed)
  - put_repo_file()        — create/update any repo file via the contents API
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Pricing ($ per MTok) — update if Anthropic pricing changes.
# Cache-write price is the 5-minute-TTL rate (1.25x input); CMA uses 5m TTL.
# ---------------------------------------------------------------------------

PRICES_PER_MTOK = {
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,
    },
    "claude-sonnet-5": {
        # Intro pricing through 2026-08-31 ($3/$15 sticker after — bump then).
        "input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50,
    },
    "claude-haiku-4-5": {
        "input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25,
    },
}

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def zero_usage() -> dict:
    return {k: 0 for k in USAGE_KEYS}


def estimate_cost_usd(usage: dict, model: str) -> float:
    """Estimate $ cost of a usage dict for the given model."""
    p = PRICES_PER_MTOK.get(model)
    if p is None:
        # Unknown model — price as Sonnet so the log errs on the high side.
        p = PRICES_PER_MTOK["claude-sonnet-4-6"]
    usd = (
        usage.get("input_tokens", 0) * p["input"]
        + usage.get("output_tokens", 0) * p["output"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
    ) / 1_000_000
    return round(usd, 4)


# ---------------------------------------------------------------------------
# URL canonicalization — mirror the agent's dedupe rules
# ---------------------------------------------------------------------------

_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|ref|fbclid|gclid|mc_cid|mc_eid|source|si|s|smid|cmpid)$", re.I
)


def canonical_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower().removeprefix("www.")
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in query if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        ("https", host, path, urllib.parse.urlencode(kept), "")
    )


# ---------------------------------------------------------------------------
# Feed prefetch
# ---------------------------------------------------------------------------

_UA = "kb-pipeline-prefetch/1.0"

# Substack's CDN 403s non-browser User-Agents from datacenter IPs, so feed
# fetches masquerade as a browser. Verified 2026-07-02: the plain bot UA got
# 403 on every *.substack.com feed from a GitHub Actions runner.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
        "text/html;q=0.8, */*;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _curl_get(url: str, timeout: int = 20) -> bytes:
    """Fetch with the curl binary — its TLS fingerprint passes Cloudflare
    checks that reject Python's urllib."""
    import subprocess

    result = subprocess.run(
        [
            "curl", "-sL", "--compressed", "--fail",
            "--max-time", str(timeout),
            "-A", _BROWSER_HEADERS["User-Agent"],
            "-H", f"Accept: {_BROWSER_HEADERS['Accept']}",
            url,
        ],
        capture_output=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl exit {result.returncode}")
    return result.stdout


def _curl_cffi_get(url: str, timeout: int = 20) -> bytes:
    """Fetch with curl_cffi impersonating Chrome's TLS handshake — the
    reliable path through Cloudflare bot detection on datacenter IPs.
    Raises ImportError if curl_cffi isn't installed."""
    from curl_cffi import requests as cf_requests

    resp = cf_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _proxy_get(url: str, timeout: int = 25) -> bytes:
    """
    Last resort: fetch through a public passthrough proxy. Substack's
    Cloudflare hard-blocks GitHub Actions IP ranges for *.substack.com
    regardless of client fingerprint (urllib, curl, and Chrome-impersonated
    curl_cffi all 403 — verified 2026-07-02), so the only client-side option
    left is a different egress IP.
    """
    encoded = urllib.parse.quote(url, safe="")
    proxies = [
        f"https://api.allorigins.win/raw?url={encoded}",
        f"https://api.codetabs.com/v1/proxy?quest={encoded}",
    ]
    last_error: Exception | None = None
    for proxy_url in proxies:
        try:
            req = urllib.request.Request(proxy_url, headers=_BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            if body.strip():
                return body
        except Exception as e:  # noqa: BLE001
            last_error = e
    raise last_error or RuntimeError("all proxies returned empty")


def _http_get(url: str, timeout: int = 20) -> bytes:
    """
    Tiered fetch: urllib (fast) → curl binary → curl_cffi (Chrome TLS
    impersonation) → public proxy (different egress IP). Substack/Cloudflare
    blocks GitHub Actions IPs for *.substack.com hosts, so 403s escalate
    through the tiers instead of failing outright.
    """
    try:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code not in (403, 429):
            raise  # real errors (404 etc.) — retrying with another client won't help
        first_error: Exception = e
    except urllib.error.URLError:
        raise

    for fetcher in (_curl_get, _curl_cffi_get, _proxy_get):
        try:
            body = fetcher(url, timeout=timeout)
            if body and body.strip():
                return body
        except Exception:  # noqa: BLE001 — ImportError, HTTP errors: next tier
            continue

    raise first_error



def _parse_date(raw: str) -> dt.datetime | None:
    """Parse RFC-822 (RSS) or ISO-8601 (Atom) dates to aware UTC datetimes."""
    if not raw:
        return None
    raw = raw.strip()
    try:  # RSS 2.0: "Tue, 01 Jul 2026 14:00:00 GMT"
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:  # Atom: "2026-07-01T14:00:00Z"
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _strip_html(text: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_feed(body: bytes) -> list[dict]:
    """
    Minimal stdlib RSS 2.0 / Atom parser. Returns entries as
    [{title, link, published (datetime|None), summary}].
    Deliberately dependency-free — feedparser's sgmllib3k dep doesn't build
    everywhere, and Substack/blog feeds are plain RSS 2.0 or Atom.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # Some feeds prepend junk (BOM, whitespace, PHP warnings) — retry from
        # the first '<'.
        text = body.decode("utf-8", errors="replace")
        idx = text.find("<")
        if idx <= 0:
            return []
        try:
            root = ET.fromstring(text[idx:])
        except ET.ParseError:
            return []

    entries: list[dict] = []

    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = _parse_date(
            item.findtext("pubDate")
            or item.findtext("{http://purl.org/dc/elements/1.1/}date")
            or ""
        )
        summary = item.findtext("description") or ""
        if link:
            entries.append({
                "title": title, "link": link,
                "published": published, "summary": _strip_html(summary),
            })
    if entries:
        return entries

    # Atom: <feed><entry>...</entry></feed>
    for entry in root.iter(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        link = ""
        for link_el in entry.findall(f"{_ATOM_NS}link"):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate" and link_el.get("href"):
                link = link_el.get("href", "")
                break
        if not link:
            first = entry.find(f"{_ATOM_NS}link")
            link = first.get("href", "") if first is not None else ""
        published = _parse_date(
            entry.findtext(f"{_ATOM_NS}published")
            or entry.findtext(f"{_ATOM_NS}updated")
            or ""
        )
        summary = (
            entry.findtext(f"{_ATOM_NS}summary")
            or entry.findtext(f"{_ATOM_NS}content")
            or ""
        )
        if link:
            entries.append({
                "title": title, "link": link,
                "published": published, "summary": _strip_html(summary),
            })
    return entries


def prefetch_feeds(
    feed_map: dict,
    since: dt.datetime,
    cutoff_days: int = 14,
    max_workers: int = 12,
    verbose: bool = True,
) -> tuple[list[dict], dict, list[dict]]:
    """
    Fetch every cached feed concurrently and return
    (candidates, updated_feed_map, failed_feeds).

    Candidates are entries newer than max(since, now - cutoff_days), each:
      {url, canonical_url, title, publication, published_at, snippet}

    failed_feeds lists {host, feed_url} for feeds that errored this run —
    the morning session fetches those in-container as a fallback (CMA egress
    IPs aren't blocked by Substack the way GitHub Actions IPs are).

    html_fallback entries and feeds with feed_url null are skipped here — the
    agent's morning discovery run covers those hosts.
    """
    now = dt.datetime.now(dt.timezone.utc)
    floor = max(since, now - dt.timedelta(days=cutoff_days))
    candidates: list[dict] = []
    failed: list[dict] = []
    updated = json.loads(json.dumps(feed_map))  # deep copy

    def fetch_one(host: str, meta: dict):
        feed_url = meta.get("feed_url")
        if not feed_url or meta.get("feed_type") == "html_fallback":
            return host, None, None
        try:
            body = _http_get(feed_url)
            return host, parse_feed(body), None
        except Exception as e:  # noqa: BLE001 — network errors of all shapes
            return host, None, e

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch_one, h, m) for h, m in feed_map.items()]
        for fut in concurrent.futures.as_completed(futures):
            host, entries, err = fut.result()
            meta = updated[host]
            if err is not None or entries is None:
                if err is not None:
                    meta["consecutive_failures"] = meta.get("consecutive_failures", 0) + 1
                    failed.append({"host": host, "feed_url": meta.get("feed_url")})
                    if verbose:
                        print(f"[prefetch] {host}: FAIL ({err})", flush=True)
                continue
            meta["consecutive_failures"] = 0
            meta["last_success"] = now.isoformat()
            fresh = 0
            for entry in entries[:20]:
                if not entry["link"] or not entry["title"]:
                    continue
                published = entry["published"]
                if published is None or published <= floor:
                    continue
                candidates.append({
                    "url": entry["link"],
                    "canonical_url": canonical_url(entry["link"]),
                    "title": entry["title"],
                    "publication": host,
                    "published_at": published.isoformat(),
                    "snippet": entry["summary"],
                })
                fresh += 1
            if verbose and fresh:
                print(f"[prefetch] {host}: {fresh} new", flush=True)

    # Dedupe candidates by canonical URL (feeds can overlap).
    seen: set[str] = set()
    unique = []
    for c in sorted(candidates, key=lambda c: c["published_at"], reverse=True):
        if c["canonical_url"] in seen:
            continue
        seen.add(c["canonical_url"])
        unique.append(c)
    return unique, updated, failed


def load_ingested_urls(jsonl_path) -> tuple[set[str], dt.datetime]:
    """Return (canonical URL set, max ingested_at) from blogs-ingested.jsonl."""
    urls: set[str] = set()
    latest = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("url"):
                    urls.add(canonical_url(row["url"]))
                ts = row.get("ingested_at")
                if ts:
                    try:
                        parsed = dt.datetime.fromisoformat(ts)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=dt.timezone.utc)
                        latest = max(latest, parsed.astimezone(dt.timezone.utc))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return urls, latest


# ---------------------------------------------------------------------------
# Haiku ranking
# ---------------------------------------------------------------------------

RANK_MODEL = "claude-haiku-4-5"

_RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "score": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["url", "score", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rankings"],
    "additionalProperties": False,
}


def rank_candidates(
    client,
    candidates: list[dict],
    interests_text: str,
    deltas_text: str,
    score_floor: int = 8,
    max_selected: int = 6,
    max_candidates: int = 80,
) -> tuple[list[dict], list[dict], dict]:
    """
    Score candidates 0-10 against the owner's profile with claude-haiku-4-5.

    Returns (selected, all_scored, usage) where selected is the score-sorted
    subset with score >= score_floor, capped at max_selected.
    """
    if not candidates:
        return [], [], zero_usage()

    pool = candidates[:max_candidates]
    listing = "\n".join(
        f"{i + 1}. [{c['publication']}] {c['title']}\n"
        f"   url: {c['url']}\n"
        f"   published: {c['published_at']}\n"
        f"   snippet: {c['snippet']}"
        for i, c in enumerate(pool)
    )

    prompt = f"""You score blog/Substack articles for relevance to the KB owner. Their interest profile:

<interest_profile>
{interests_text}
</interest_profile>

<profile_deltas>
{deltas_text or '(none)'}
</profile_deltas>

Score each candidate 0-10:
- 9-10: directly advances a core theme (frontier AI capabilities/economics, agent architecture & reliability, AI labor economics, Anthropic platform strategy, AI governance) with original argument, data, or mechanism.
- 8: clearly on-theme with real depth.
- 5-7: on-theme but shallow, news-recap, or link-roundup.
- 0-4: off-theme, promotional, or pure aggregation.
Penalize link-aggregator posts and event announcements. Reward original analysis, contrarian argument, and mechanistic depth. Give a one-line rationale per item.

Candidates:
{listing}

Return a score for every candidate."""

    resp = client.messages.create(
        model=RANK_MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": _RANK_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    usage = {k: getattr(resp.usage, k, 0) or 0 for k in USAGE_KEYS}

    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        rankings = json.loads(text).get("rankings", [])
    except json.JSONDecodeError:
        rankings = []

    by_url = {canonical_url(r["url"]): r for r in rankings if r.get("url")}
    scored = []
    for c in pool:
        r = by_url.get(c["canonical_url"])
        if r is None:
            continue
        scored.append({**c, "score": r["score"], "rationale": r["rationale"]})
    scored.sort(key=lambda c: c["score"], reverse=True)
    selected = [c for c in scored if c["score"] >= score_floor][:max_selected]
    return selected, scored, usage


# ---------------------------------------------------------------------------
# GitHub contents API helpers (no local git required)
# ---------------------------------------------------------------------------


def _repo_path_from_url(repo_url: str) -> str:
    return repo_url.removeprefix("https://github.com/").rstrip("/")


def _contents_request(url: str, token: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": _UA,
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def put_repo_file(
    repo_url: str,
    token: str,
    path: str,
    content: str,
    message: str,
    retries: int = 3,
) -> bool:
    """Create or update a repo file via the contents API (sha-based, retried)."""
    import base64

    repo_path = _repo_path_from_url(repo_url)
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{path}"
    for attempt in range(retries):
        sha = None
        try:
            sha = _contents_request(api_url, token).get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha
        try:
            _contents_request(api_url, token, method="PUT", payload=payload)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))  # sha race — refetch and retry
                continue
            print(f"[costlog/put] PUT {path} failed: {e}", flush=True)
            return False
    return False


def get_repo_file(repo_url: str, token: str, path: str) -> str | None:
    """Fetch a repo file's decoded content, or None on 404."""
    import base64

    repo_path = _repo_path_from_url(repo_url)
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{path}"
    try:
        data = _contents_request(api_url, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return base64.b64decode(data["content"].replace("\n", "")).decode()


COST_LOG_PATH = "_system/logs/costs.jsonl"

# In-process memory of the last successfully-written log lines. Guards against
# GitHub contents-API read-after-write lag: a GET issued ~1s after a PUT can
# 404 or return stale content, and consecutive appends in the same run (e.g.
# blog-rank then blog) would silently drop the earlier line without this.
_costlog_lines: list[str] = []


def _get_file_with_sha(repo_url: str, token: str, path: str) -> tuple[str | None, str]:
    """Single GET returning (sha, decoded content). sha None on 404."""
    import base64

    repo_path = _repo_path_from_url(repo_url)
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{path}"
    try:
        data = _contents_request(api_url, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, ""
        raise
    return data.get("sha"), base64.b64decode(data["content"].replace("\n", "")).decode()


def append_cost_log(
    repo_url: str,
    token: str,
    pipeline: str,
    slot: str,
    model: str,
    usage: dict,
    session_id: str | None = None,
    note: str | None = None,
) -> None:
    """
    Append one JSONL cost line to _system/logs/costs.jsonl via the contents API.
    Never raises — cost logging must not fail the run.
    """
    import base64

    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "pipeline": pipeline,
        "slot": slot,
        "model": model,
        "session_id": session_id,
        **{k: usage.get(k, 0) for k in USAGE_KEYS},
        "est_usd": estimate_cost_usd(usage, model),
    }
    if note:
        entry["note"] = note
    entry_line = json.dumps(entry)

    repo_path = _repo_path_from_url(repo_url)
    api_url = f"https://api.github.com/repos/{repo_path}/contents/{COST_LOG_PATH}"

    try:
        for attempt in range(4):
            # ONE fetch for both sha and content — a separate sha fetch can see
            # a different (fresher) state than the content fetch and clobber.
            sha, remote_text = _get_file_with_sha(repo_url, token, COST_LOG_PATH)
            remote_lines = [l for l in remote_text.splitlines() if l.strip()]
            # Union with lines this process already wrote (order-preserving),
            # in case the GET was stale.
            merged = remote_lines + [l for l in _costlog_lines if l not in remote_lines]
            merged.append(entry_line)

            payload = {
                "message": f"costlog: {pipeline}/{slot} ${entry['est_usd']}",
                "content": base64.b64encode(("\n".join(merged) + "\n").encode()).decode(),
            }
            if sha:
                payload["sha"] = sha
            try:
                _contents_request(api_url, token, method="PUT", payload=payload)
                _costlog_lines.clear()
                _costlog_lines.extend(merged)
                print(f"[costlog] {pipeline}/{slot} {model} → ${entry['est_usd']}", flush=True)
                return
            except urllib.error.HTTPError as e:
                # 409: sha race with a concurrent writer. 422: our GET saw a
                # stale 404 (no sha) but the file exists. Both: wait, refetch.
                if e.code in (409, 422) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
    except Exception as e:  # noqa: BLE001
        print(f"[costlog] append failed (non-fatal): {e}", flush=True)
