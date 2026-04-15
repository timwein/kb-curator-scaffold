"""
Build _system/profile/bookmark-taste-profile.md in the KB repo.

This is the PRIMARY reference the bookmarking agent reads each run to
decide what to bookmark. It aggregates the user's bookmarking history (tweet
analyses in YYYY/MM/DD/) plus seed signals (_system/seed/*.md filenames,
which are descriptive slugs from past Claude.ai conversations) into a
structured markdown document.

USAGE
-----
    export GITHUB_PAT=github_pat_...
    python build_taste_profile.py

One-shot. Re-run whenever the user wants to refresh the profile (e.g., after
interests visibly shift or a month of new analyses accumulate).

IMPLEMENTATION NOTES
--------------------
Clones the KB repo to a temp dir, walks analysis files on disk (faster
and cheaper than 300+ HTTP calls), aggregates, writes the profile, then
commits and pushes.

Does NOT use GitHub's raw-file API except for reading the clone URL.
The commit is authored as "tweet-bookmarker-setup" via the PAT.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


GITHUB_REPO_URL = "https://github.com/<your-username>/<your-kb-repo>"
GITHUB_REPO_BRANCH = "main"
PROFILE_PATH = "_system/profile/bookmark-taste-profile.md"
COMMIT_MESSAGE = "taste profile: rebuild from latest analyses + seed corpus"

# YYYY-prefixed dirs are where analysis files live (2024/, 2026/, ...).
YEAR_DIR_RE = re.compile(r"^\d{4}$")
# Analysis filename pattern: <tweet_id>-<author-slug>-<short-slug>.md
# or blog-<slug>.md (for blog-ingestion files) or run-log-*.md, etc.
# We want tweet analyses and blog analyses only — not run-log files.
ANALYSIS_FILENAME_RE = re.compile(
    r"^(?P<kind>blog|\d+)-[^.]+\.md$"
)
# Match YAML frontmatter inside a <details> block — captures the entire
# key:value content line-by-line.
YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)
# Match a "## My Take" section up to the next H2 or EOF.
MY_TAKE_RE = re.compile(r"##\s+My Take\s*\n(.*?)(?=\n##\s+|\Z)", re.DOTALL)
# Match the leading bold TLDR sentence: **Something...** (first bold run)
BOLD_LEAD_RE = re.compile(r"\*\*([^*]{40,400})\*\*")

SEED_SLUG_STOPWORDS = {
    "the",
    "and",
    "a",
    "of",
    "in",
    "for",
    "to",
    "on",
    "with",
    "as",
    "an",
    "is",
    "are",
    "at",
    "by",
    "ai",  # too generic for a top-N signal
    "how",
    "why",
    "what",
    "when",
    "where",
    "this",
    "that",
    "its",
    "it",
    "s",
    "vs",
    "from",
    "about",
}


def run(cmd: list[str], cwd: str | None = None, env: dict | None = None) -> None:
    """Run a subprocess, streaming output. Raises on non-zero exit."""
    subprocess.run(cmd, cwd=cwd, env=env, check=True, text=True)


def clone_repo(pat: str, dest: Path) -> None:
    """Shallow-clone the KB repo to `dest` using the given PAT."""
    url_with_pat = GITHUB_REPO_URL.replace(
        "https://", f"https://x-access-token:{pat}@"
    )
    run(
        [
            "git",
            "clone",
            "--depth=1",
            "--branch",
            GITHUB_REPO_BRANCH,
            url_with_pat,
            str(dest),
        ]
    )


def parse_analysis_file(path: Path) -> dict[str, Any] | None:
    """Extract taste-relevant fields from one analysis .md file.

    Returns a dict or None if the file doesn't look like an analysis
    (e.g., a run-log, an index, or a seed file that landed here by mistake).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    yaml_m = YAML_BLOCK_RE.search(text)
    if not yaml_m:
        return None
    yaml_text = yaml_m.group(1)

    # Pull fields out of the YAML by line — tiny parser so we don't
    # require PyYAML as a new dep.
    fields: dict[str, Any] = {}
    for line in yaml_text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip().strip("\"'")

    author = fields.get("author")
    if not author:
        return None

    relevance: int | None = None
    try:
        relevance = int(fields.get("relevance_score") or "")
    except (TypeError, ValueError):
        pass

    # topics is a YAML list literal like ["a", "b", "c"] on one line.
    topics_raw = fields.get("topics") or ""
    topics = [t.strip().strip("\"'") for t in re.findall(r"\"([^\"]+)\"", topics_raw)]

    tldr_snippet = ""
    bold_m = BOLD_LEAD_RE.search(text)
    if bold_m:
        tldr_snippet = bold_m.group(1).strip()

    my_take_snippet = ""
    take_m = MY_TAKE_RE.search(text)
    if take_m:
        body = take_m.group(1).strip()
        # First sentence only, trimmed.
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0]
        my_take_snippet = first_sentence[:300]

    return {
        "path": path,
        "author": author,
        "relevance": relevance,
        "topics": topics,
        "tldr": tldr_snippet,
        "my_take": my_take_snippet,
        "source_type": fields.get("source_type", "tweet"),
        "url": fields.get("url", ""),
    }


def walk_analyses(repo_root: Path) -> list[dict[str, Any]]:
    """Find all analysis files in YYYY/MM/DD/ subtrees."""
    analyses: list[dict[str, Any]] = []
    for year_dir in sorted(repo_root.iterdir()):
        if not year_dir.is_dir() or not YEAR_DIR_RE.match(year_dir.name):
            continue
        for md_path in year_dir.rglob("*.md"):
            name = md_path.name
            # Skip non-analysis markdown: run-logs, READMEs, blog-synthesis,
            # blog agent logs.
            if name.lower() in ("readme.md",):
                continue
            if name.startswith("run-log-") or name.startswith("blog-synthesis"):
                continue
            a = parse_analysis_file(md_path)
            if a is not None:
                analyses.append(a)
    return analyses


def seed_topic_tokens(repo_root: Path) -> Counter:
    """Extract topic tokens from seed/*.md filenames.

    Each seed filename is a descriptive slug like
    `ai-job-displacement-concerns-overblown.md` — the slug words are the
    topic signal. We skip the leading hash-prefix (first hyphen-delimited
    token) which is the conversation ID.
    """
    seed_dir = repo_root / "_system" / "seed"
    if not seed_dir.is_dir():
        return Counter()
    tokens = Counter()
    for p in seed_dir.glob("*.md"):
        stem = p.stem  # filename without .md
        # Filenames are `<hash>-<slug-words>`. Drop the first token.
        parts = stem.split("-")
        if len(parts) < 2:
            continue
        slug_tokens = parts[1:]
        for tok in slug_tokens:
            tok = tok.strip().lower()
            if not tok or tok in SEED_SLUG_STOPWORDS or len(tok) < 3:
                continue
            tokens[tok] += 1
    return tokens


def rank_authors(analyses: list[dict[str, Any]]) -> list[tuple[str, int, float, Path]]:
    """Return authors ranked by count × avg_relevance.

    Returns list of (author, count, avg_relevance, example_path) — the
    example_path is the highest-relevance analysis for that author.
    """
    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in analyses:
        author = a["author"]
        if not author.startswith("@"):
            continue  # blog analyses have different author shapes
        by_author[author].append(a)

    ranked = []
    for author, items in by_author.items():
        relevances = [x["relevance"] for x in items if x["relevance"] is not None]
        if not relevances:
            continue
        avg = sum(relevances) / len(relevances)
        count = len(items)
        # Example analysis: highest relevance, tiebreak by most recent (path sort desc)
        example = sorted(
            items,
            key=lambda x: (x["relevance"] or 0, str(x["path"])),
            reverse=True,
        )[0]["path"]
        ranked.append((author, count, avg, example))
    # Sort by count × avg (prioritizes repeated-high-relevance authors)
    ranked.sort(key=lambda t: t[1] * t[2], reverse=True)
    return ranked


def rank_topics(analyses: list[dict[str, Any]]) -> list[tuple[str, int, Path]]:
    """Return topics ranked by count. Example is the highest-relevance analysis touching that topic."""
    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in analyses:
        for t in a["topics"]:
            by_topic[t].append(a)

    ranked = []
    for topic, items in by_topic.items():
        example = sorted(
            items,
            key=lambda x: (x["relevance"] or 0, str(x["path"])),
            reverse=True,
        )[0]["path"]
        ranked.append((topic, len(items), example))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def pick_exemplars(
    analyses: list[dict[str, Any]], high: bool
) -> list[dict[str, Any]]:
    """Pick 6 exemplars for calibration. If high=True, score >= 8; if False, score <= 4."""
    if high:
        candidates = [a for a in analyses if (a["relevance"] or 0) >= 8]
    else:
        candidates = [a for a in analyses if 0 < (a["relevance"] or 0) <= 4]
    # Diversify by author — one per author until we have 6.
    seen_authors: set[str] = set()
    picks: list[dict[str, Any]] = []
    for a in sorted(candidates, key=lambda x: -(x["relevance"] or 0)):
        if a["author"] in seen_authors:
            continue
        picks.append(a)
        seen_authors.add(a["author"])
        if len(picks) >= 6:
            break
    return picks


def path_to_kb_link(repo_root: Path, file_path: Path) -> str:
    """Convert an absolute path inside the repo clone to a KB-relative link."""
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        return str(file_path)
    return str(rel)


def render_profile(
    analyses: list[dict[str, Any]],
    author_ranks: list[tuple[str, int, float, Path]],
    topic_ranks: list[tuple[str, int, Path]],
    seed_tokens: Counter,
    repo_root: Path,
) -> str:
    """Render the bookmark-taste-profile.md content."""
    total = len(analyses)
    high_rel = sum(1 for a in analyses if (a["relevance"] or 0) >= 8)
    avg_rel = (
        sum(a["relevance"] for a in analyses if a["relevance"] is not None)
        / max(1, sum(1 for a in analyses if a["relevance"] is not None))
    )

    lines: list[str] = []
    lines.append("# the user's Bookmark Taste Profile")
    lines.append("")
    lines.append(
        "*Generated from the user's bookmarking history. "
        f"{total} analyses corpus · avg relevance {avg_rel:.1f}/10 · "
        f"{high_rel} high-relevance (8+) · {len(author_ranks)} distinct authors · "
        f"{len(topic_ranks)} topics*"
    )
    lines.append("")
    lines.append(
        "This document is the single source of truth for what the user tends to bookmark. "
        "The bookmarking agent reads it before every run. It's generated by "
        "`build_taste_profile.py` from the analyses in this repo; re-run that script "
        "to refresh."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Patterns prose ------------------------------------------------------
    lines.append("## What the user Bookmarks (in prose)")
    lines.append("")
    lines.append(
        "the user is a venture investor. His bookmarks skew heavily toward: "
        "dense technical or macro-strategic signal from credible operators, "
        "field reports that beat theory pieces, counterintuitive takes that "
        "invert consensus, long-form formats (X Articles, high-effort threads), "
        "and arguments with non-obvious second-order implications. He bookmarks "
        "for use in investor calls, VC roundtables, founder conversations, and "
        "his own writing — so anything he bookmarks should be something he can "
        "*quote*, not just read."
    )
    lines.append("")
    lines.append("**What scores 9-10 (bookmark unhesitatingly):**")
    lines.append("")
    lines.append(
        "- Operator field reports with specific observations (e.g. \"I visited five companies and here's what I saw\")"
    )
    lines.append(
        "- Sharp technical claims from practitioners with novel mechanism stories"
    )
    lines.append(
        "- Contrarian framings backed by data or direct experience, not vibes"
    )
    lines.append("- Second-order-effects analyses that name what others haven't said out loud")
    lines.append("- X Articles (longform essays) from already-favored authors")
    lines.append("")
    lines.append("**What scores 3-5 (skip):**")
    lines.append("")
    lines.append("- Consensus restated with no new angle")
    lines.append("- Breaking news without analytical framing")
    lines.append("- Self-promotion (\"we raised!\", \"I shipped!\")")
    lines.append("- Memes / jokes / engagement bait / culture-war content")
    lines.append("- Tweets from authors who aren't in the user's favored-author list, without unusually strong substance to compensate")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Favored authors ----------------------------------------------------
    lines.append("## Favored Authors")
    lines.append("")
    lines.append(
        "Ranked by `count × avg_relevance`. Treat the top ~20 as strong priors: "
        "if one of them posts a thread root, that's already ~60-70% of the way "
        "to a bookmark decision."
    )
    lines.append("")
    lines.append("| Author | Analyses | Avg Relevance | Example |")
    lines.append("|---|---:|---:|---|")
    for author, count, avg, example in author_ranks[:40]:
        ex_link = path_to_kb_link(repo_root, example)
        lines.append(f"| {author} | {count} | {avg:.1f} | [{example.name}]({ex_link}) |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Favored topics ----------------------------------------------------
    lines.append("## Favored Topics")
    lines.append("")
    lines.append(
        "Topics from analysis frontmatter, ranked by analysis count. A tweet "
        "touching two or more of these topics is a strong signal."
    )
    lines.append("")
    lines.append("| Topic | Analyses | Example |")
    lines.append("|---|---:|---|")
    for topic, count, example in topic_ranks[:20]:
        ex_link = path_to_kb_link(repo_root, example)
        lines.append(f"| `{topic}` | {count} | [{example.name}]({ex_link}) |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Calibration exemplars ----------------------------------------------
    lines.append("## High-Relevance Calibration Examples (score 8+)")
    lines.append("")
    lines.append(
        "Concrete examples of what a 'bookmark-worthy' tweet analysis looks like. "
        "Use these to calibrate judgment on borderline candidates."
    )
    lines.append("")
    hi = pick_exemplars(analyses, high=True)
    for a in hi:
        lines.append(f"- **{a['author']} (relevance {a['relevance']}/10)** — "
                     f"[{a['path'].name}]({path_to_kb_link(repo_root, a['path'])})  ")
        if a["tldr"]:
            lines.append(f"  > {a['tldr'][:250]}")
        lines.append("")

    lo = pick_exemplars(analyses, high=False)
    if lo:
        lines.append("## Low-Relevance Anti-Examples (score ≤4)")
        lines.append("")
        lines.append(
            "The kind of content that technically got ingested but shouldn't have "
            "cleared the bookmarking bar. Use these as anti-patterns — if a candidate "
            "feels like these, skip it."
        )
        lines.append("")
        for a in lo:
            lines.append(f"- **{a['author']} (relevance {a['relevance']}/10)** — "
                         f"[{a['path'].name}]({path_to_kb_link(repo_root, a['path'])})  ")
            if a["tldr"]:
                lines.append(f"  > {a['tldr'][:250]}")
            lines.append("")

    lines.append("---")
    lines.append("")

    # --- Seed signals -------------------------------------------------------
    lines.append("## Seed Signals")
    lines.append("")
    lines.append(
        "Themes extracted from 200 past Claude.ai conversations the user had about "
        "articles and research. Tokens below appear in 2+ conversation titles — "
        "strong historical interest signal even for things not yet in the tweet KB."
    )
    lines.append("")
    top_seed = [t for t in seed_tokens.most_common(40) if t[1] >= 2]
    if top_seed:
        pill_strs = [f"`{tok}` ×{n}" for tok, n in top_seed]
        lines.append("  " + " · ".join(pill_strs))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*End of profile. Regenerate via `python build_taste_profile.py` after "
        "meaningful taste drift.*"
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    pat = os.environ.get("GITHUB_PAT")
    if not pat:
        sys.exit("ERROR: GITHUB_PAT is not set in the environment.")

    workdir = Path(tempfile.mkdtemp(prefix="kb-taste-"))
    try:
        print(f"cloning KB repo into {workdir}...", flush=True)
        clone_repo(pat, workdir)

        print("walking analysis files...", flush=True)
        analyses = walk_analyses(workdir)
        print(f"  found {len(analyses)} analysis files", flush=True)
        if not analyses:
            sys.exit("ERROR: no analyses found — can't build a taste profile")

        print("ranking authors...", flush=True)
        author_ranks = rank_authors(analyses)
        print(f"  {len(author_ranks)} distinct authors", flush=True)

        print("ranking topics...", flush=True)
        topic_ranks = rank_topics(analyses)
        print(f"  {len(topic_ranks)} distinct topics", flush=True)

        print("extracting seed topic tokens...", flush=True)
        seed_tokens = seed_topic_tokens(workdir)
        print(f"  {len(seed_tokens)} distinct seed tokens", flush=True)

        print("rendering profile...", flush=True)
        content = render_profile(
            analyses=analyses,
            author_ranks=author_ranks,
            topic_ranks=topic_ranks,
            seed_tokens=seed_tokens,
            repo_root=workdir,
        )
        print(f"  rendered {len(content):,} chars", flush=True)

        profile_file = workdir / PROFILE_PATH
        profile_file.parent.mkdir(parents=True, exist_ok=True)
        profile_file.write_text(content, encoding="utf-8")

        print("committing + pushing...", flush=True)
        # Configure identity for this commit (local to this clone only).
        # Set user.email to an email GitHub has verified on your account so
        # Vercel's committer-to-GitHub-user check passes; keeping the
        # distinct user.name so bookmarker-setup commits are still
        # identifiable in git log.
        git_email = os.environ.get("GIT_COMMITTER_EMAIL")
        if not git_email:
            raise SystemExit(
                "GIT_COMMITTER_EMAIL env var must be set to a "
                "GitHub-verified email on your account."
            )
        run(["git", "config", "user.email", git_email], cwd=str(workdir))
        run(["git", "config", "user.name", "tweet-bookmarker-setup"], cwd=str(workdir))
        run(["git", "add", PROFILE_PATH], cwd=str(workdir))
        # Check if there's actually something to commit (the profile may
        # be byte-identical to what's already there).
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workdir),
            check=True,
            text=True,
            capture_output=True,
        )
        if not status.stdout.strip():
            print("  no changes — profile is already up to date", flush=True)
            return
        run(["git", "commit", "-m", COMMIT_MESSAGE], cwd=str(workdir))
        run(["git", "push", "origin", GITHUB_REPO_BRANCH], cwd=str(workdir))
        print("done.", flush=True)
        print()
        print(f"Profile committed at {PROFILE_PATH}")
        print(f"View: {GITHUB_REPO_URL}/blob/{GITHUB_REPO_BRANCH}/{PROFILE_PATH}")

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
