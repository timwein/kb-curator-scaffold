"""
ONE-TIME SEEDING — parse a claude.ai data export and populate kb/seed/
with conversations that look like article analyses.

HOW TO GET THE EXPORT:
  1. Go to https://claude.ai/ → Settings → Privacy → "Export data"
  2. Wait for the email (usually a few minutes)
  3. Download and extract the ZIP — you want conversations.json

USAGE:
  # Clone your KB repo locally so we can write files into it:
  git clone https://github.com/<your-username>/<your-kb-repo> ~/tmp/kb-clone

  # Run the seeder (dry run first to see what would happen):
  python seed_from_claude_export.py /path/to/conversations.json \\
      --kb-path ~/tmp/kb-clone --dry-run

  # If it looks right, drop --dry-run:
  python seed_from_claude_export.py /path/to/conversations.json \\
      --kb-path ~/tmp/kb-clone

  # Then manually commit and push:
  cd ~/tmp/kb-clone
  git add seed/ meta/seeded.jsonl
  git commit -m "seed KB from claude.ai export"
  git push

FILTERING HEURISTIC: a conversation is kept if its first human message either
  (a) contains at least one URL, or
  (b) is longer than 500 characters
— on the assumption that those are the chats where you shared an article /
blog / substack / paper to analyze. Tweak --min-chars if you want a different
threshold, or edit `looks_like_article_analysis` below to refine further.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


URL_RE = re.compile(r"https?://[^\s)\]\"<>]+")
DEFAULT_MIN_PASTED_CHARS = 500
SLUG_MAX_LEN = 60


def looks_like_article_analysis(first_user_text: str, min_chars: int) -> bool:
    if not first_user_text:
        return False
    if URL_RE.search(first_user_text):
        return True
    if len(first_user_text) >= min_chars:
        return True
    return False


def slugify(text: str, max_len: int = SLUG_MAX_LEN) -> str:
    text = re.sub(r"[^\w\s-]", "", text or "").strip().lower()
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def first_user_text(conv: dict) -> str:
    for msg in conv.get("chat_messages", []) or []:
        if msg.get("sender") != "human":
            continue
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                return block.get("text", "") or ""
        # Some exports put plain text directly on the message
        if isinstance(msg.get("text"), str):
            return msg["text"]
        return ""
    return ""


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def render_seed_markdown(conv: dict) -> str:
    """Render a conversation as a single markdown file."""
    conv_id = conv.get("uuid") or conv.get("id") or "unknown"
    title = (conv.get("name") or "").strip() or "(untitled)"
    created = conv.get("created_at") or ""
    first_text = first_user_text(conv)
    source_urls = extract_urls(first_text)

    # Escape quotes inside YAML string values
    def yaml_str(s: str) -> str:
        return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines: list[str] = ["---"]
    lines.append(f"conversation_id: {yaml_str(conv_id)}")
    lines.append(f"title: {yaml_str(title)}")
    lines.append(f"created_at: {yaml_str(created)}")
    lines.append('source: "claude.ai-export"')
    if source_urls:
        lines.append("source_urls:")
        for u in source_urls[:5]:
            lines.append(f"  - {yaml_str(u)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    for msg in conv.get("chat_messages", []) or []:
        sender = msg.get("sender", "unknown")
        role = "the user" if sender == "human" else "Claude" if sender == "assistant" else sender
        parts: list[str] = []
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", "") or "")
        if not parts and isinstance(msg.get("text"), str):
            parts.append(msg["text"])
        combined = "\n".join(parts).strip()
        if not combined:
            continue
        lines.append(f"## {role}")
        lines.append("")
        lines.append(combined)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export_path", type=Path, help="Path to conversations.json from claude.ai export")
    parser.add_argument(
        "--kb-path",
        type=Path,
        required=True,
        help="Path to a local clone of the KB repo (we write to <kb-path>/seed/)",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=DEFAULT_MIN_PASTED_CHARS,
        help=f"Minimum first-message length to keep a conversation (default {DEFAULT_MIN_PASTED_CHARS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write any files; just report what would happen",
    )
    args = parser.parse_args()

    if not args.export_path.exists():
        raise SystemExit(f"Export file not found: {args.export_path}")
    if not args.kb_path.exists():
        raise SystemExit(f"KB path not found: {args.kb_path}")

    conversations = json.loads(args.export_path.read_text())
    if not isinstance(conversations, list):
        raise SystemExit("Expected conversations.json to be a JSON array at the top level.")

    seed_dir = args.kb_path / "seed"
    meta_dir = args.kb_path / "meta"
    if not args.dry_run:
        seed_dir.mkdir(parents=True, exist_ok=True)
        meta_dir.mkdir(parents=True, exist_ok=True)

    kept: list[dict] = []
    skipped = 0

    for conv in conversations:
        first_text = first_user_text(conv)
        if not looks_like_article_analysis(first_text, args.min_chars):
            skipped += 1
            continue

        conv_id = conv.get("uuid") or conv.get("id") or f"conv-{len(kept)}"
        title = (conv.get("name") or "").strip() or first_text[:60]
        slug = slugify(title)
        short_id = re.sub(r"[^A-Za-z0-9]", "", conv_id)[:12] or "noid"
        filename = f"{short_id}-{slug}.md"
        out_path = seed_dir / filename

        content = render_seed_markdown(conv)
        if not args.dry_run:
            out_path.write_text(content)

        kept.append(
            {
                "conversation_id": conv_id,
                "title": title,
                "seed_path": f"seed/{filename}",
                "seeded_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    if not args.dry_run and kept:
        seeded_log = meta_dir / "seeded.jsonl"
        with seeded_log.open("a") as f:
            for entry in kept:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Scanned:  {len(conversations)} conversations")
    print(f"Kept:     {len(kept)}")
    print(f"Skipped:  {skipped} (didn't look like article analyses)")
    if args.dry_run:
        print("\n(DRY RUN — no files written. Re-run without --dry-run to apply.)")
    else:
        print(f"\nWrote {len(kept)} files to: {seed_dir}")
        print(f"Appended {len(kept)} entries to: {meta_dir / 'seeded.jsonl'}")
        print("\nNext steps:")
        print(f"  cd {args.kb_path}")
        print("  git add seed/ meta/seeded.jsonl")
        print("  git commit -m 'seed KB from claude.ai export'")
        print("  git push")


if __name__ == "__main__":
    main()
