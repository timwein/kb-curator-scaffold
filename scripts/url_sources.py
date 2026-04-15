#!/usr/bin/env python3
"""
URL corpus → source/author/publication leaderboard.
Reads URLs from a text/markdown file, normalizes them, classifies by source
type, extracts author/publication from URL structure, and optionally fetches
pages to resolve titles and bylines for non-obvious sources.
Usage:
    python url_sources.py claude_messages_clean.md
    python url_sources.py claude_messages_clean.md --fetch    # slower, richer
"""
import re
import sys
import json
import argparse
from collections import Counter, defaultdict
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from pathlib import Path
URL_RE = re.compile(r"https?://[^\s\)\]\}>\"'`]+")
# trailing punctuation that often gets caught in URL regex
TRAILING_JUNK = ".,;:!?)]}>"
# query params that don't change destination
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "ref_url", "source", "mc_cid", "mc_eid", "fbclid",
    "gclid", "igshid", "si", "feature", "publication_id", "post_id",
    "isFreemail", "r", "triedRedirect", "showWelcomeOnShare",
}
def clean_url(url: str) -> str:
    url = url.rstrip(TRAILING_JUNK)
    try:
        p = urlparse(url)
    except Exception:
        return url
    netloc = p.netloc.lower().lstrip("www.")
    # twitter → x consolidation
    if netloc in ("twitter.com", "mobile.twitter.com"):
        netloc = "x.com"
    # strip tracking params
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING_PARAMS]
    path = p.path.rstrip("/")
    return urlunparse((p.scheme or "https", netloc, path, "", urlencode(q), ""))
def classify(url: str) -> dict:
    """Return {source_type, publication, author, slug} from URL structure alone."""
    p = urlparse(url)
    host = p.netloc.lower().lstrip("www.")
    parts = [seg for seg in p.path.split("/") if seg]
    out = {
        "url": url,
        "host": host,
        "source_type": "other",
        "publication": host,
        "author": None,
        "slug": parts[-1] if parts else "",
    }
    # --- Substack: {handle}.substack.com OR custom domain w/ /p/{slug}
    if host.endswith(".substack.com"):
        handle = host.split(".")[0]
        out.update(source_type="substack", publication=handle, author=handle)
        return out
    if "/p/" in p.path and len(parts) >= 2 and parts[0] == "p":
        # custom-domain substack: e.g. exponentialview.co/p/...
        out.update(source_type="substack", publication=host, author=host.split(".")[0])
        return out
    # substack open.substack.com/pub/{handle}/p/{slug}
    if host == "open.substack.com" and len(parts) >= 2 and parts[0] == "pub":
        out.update(source_type="substack", publication=parts[1], author=parts[1])
        return out
    # --- X / Twitter
    if host == "x.com" and parts:
        out.update(source_type="x", publication="x.com", author=parts[0])
        return out
    # --- arXiv
    if host in ("arxiv.org", "www.arxiv.org"):
        out.update(source_type="arxiv", publication="arXiv")
        return out
    # --- GitHub
    if host == "github.com" and len(parts) >= 1:
        out.update(source_type="github", publication="GitHub", author=parts[0])
        return out
    # --- YouTube
    if host in ("youtube.com", "youtu.be", "m.youtube.com"):
        out.update(source_type="youtube", publication="YouTube")
        return out
    # --- LinkedIn
    if "linkedin.com" in host:
        if len(parts) >= 2 and parts[0] in ("in", "company"):
            out.update(source_type="linkedin", publication="LinkedIn", author=parts[1])
        else:
            out.update(source_type="linkedin", publication="LinkedIn")
        return out
    # --- Medium
    if host == "medium.com" and parts:
        author = parts[0].lstrip("@")
        out.update(source_type="medium", publication="Medium", author=author)
        return out
    if host.endswith(".medium.com"):
        out.update(source_type="medium", publication="Medium",
                   author=host.split(".")[0])
        return out
    # --- known publications (extend as needed)
    KNOWN = {
        "anthropic.com": ("Anthropic", "lab"),
        "openai.com": ("OpenAI", "lab"),
        "deepmind.google": ("DeepMind", "lab"),
        "factory.ai": ("Factory", "company"),
        "nytimes.com": ("NYT", "news"),
        "wsj.com": ("WSJ", "news"),
        "ft.com": ("FT", "news"),
        "bloomberg.com": ("Bloomberg", "news"),
        "theinformation.com": ("The Information", "news"),
        "stratechery.com": ("Stratechery", "blog"),
        "simonwillison.net": ("Simon Willison", "blog"),
        "lesswrong.com": ("LessWrong", "forum"),
        "alignmentforum.org": ("Alignment Forum", "forum"),
    }
    for domain, (pub, kind) in KNOWN.items():
        if host == domain or host.endswith("." + domain):
            out.update(source_type=kind, publication=pub)
            return out
    # default: treat as company/blog
    out["source_type"] = "blog"
    return out
def maybe_fetch_title(url: str, timeout=6):
    """Optional: fetch page and extract <title> + meta author. Best-effort."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(200_000).decode("utf-8", errors="ignore")
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        author_m = re.search(
            r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)',
            html, re.I,
        )
        return {
            "title": title_m.group(1).strip() if title_m else None,
            "author": author_m.group(1).strip() if author_m else None,
        }
    except Exception:
        return {"title": None, "author": None}
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="text/markdown file containing URLs")
    ap.add_argument("--fetch", action="store_true",
                    help="fetch each unique URL to enrich title/author (slow)")
    ap.add_argument("--out", default="url_sources.md")
    ap.add_argument("--json", default="url_sources.json")
    args = ap.parse_args()
    text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
    raw_urls = URL_RE.findall(text)
    def _safe_clean(u):
        try:
            return clean_url(u)
        except Exception:
            return None
    cleaned = [c for c in (_safe_clean(u) for u in raw_urls) if c]
    counts = Counter(cleaned)
    records = []
    for url, n in counts.most_common():
        try:
            info = classify(url)
        except Exception:
            continue
        info["count"] = n
        if args.fetch:
            info.update({k: v for k, v in maybe_fetch_title(url).items() if v})
        records.append(info)
    # group by publication
    by_pub = defaultdict(list)
    for r in records:
        by_pub[r["publication"]].append(r)
    pub_ranked = sorted(
        by_pub.items(),
        key=lambda kv: sum(r["count"] for r in kv[1]),
        reverse=True,
    )
    # markdown report
    out = ["# URL Sources Leaderboard", ""]
    out.append(f"_{len(records)} unique URLs across {len(by_pub)} publications_")
    out.append("")
    out.append("## By Publication (frequency-ranked)")
    out.append("")
    for pub, items in pub_ranked:
        total = sum(r["count"] for r in items)
        kinds = {r["source_type"] for r in items}
        authors = sorted({r["author"] for r in items if r["author"]})
        author_str = f" — authors: {', '.join(authors)}" if authors else ""
        out.append(f"### {pub}  ({total} links, {','.join(kinds)}){author_str}")
        for r in sorted(items, key=lambda x: -x["count"]):
            label = r.get("title") or r["slug"] or r["url"]
            out.append(f"- [{r['count']}x] {label} — <{r['url']}>")
        out.append("")
    out.append("## By Source Type")
    out.append("")
    by_type = Counter()
    for r in records:
        by_type[r["source_type"]] += r["count"]
    for t, n in by_type.most_common():
        out.append(f"- **{t}**: {n}")
    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    Path(args.json).write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"✓ {len(raw_urls)} URLs found, {len(records)} unique")
    print(f"✓ {len(by_pub)} publications")
    print(f"✓ markdown → {args.out}")
    print(f"✓ json     → {args.json}")
if __name__ == "__main__":
    main()
