#!/usr/bin/env python3
"""
Migrate the KB repo from content-type-first to date-first layout.

Old:  analyses/YYYY/MM/DD/<file>.md, syntheses/..., logs/..., meta/, profile/, seed/
New:  YYYY/MM/DD/<file>.md, _system/meta/, _system/profile/, _system/seed/

Uses the Git Trees API to do the entire migration in a single atomic commit.
"""

import os
import re
import json
import httpx

REPO = "<your-username>/<your-kb-repo>"
PAT = os.environ["GITHUB_PAT"]
HEADERS = {
    "Authorization": f"Bearer {PAT}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
BASE = f"https://api.github.com/repos/{REPO}"


def api(method, path, **kwargs):
    resp = getattr(httpx, method)(f"{BASE}{path}", headers=HEADERS, timeout=30, **kwargs)
    if resp.status_code >= 400:
        print(f"  API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def remap_path(old_path: str):
    """Return the new path, or None to delete the file."""

    # --- analyses/YYYY/MM/DD/<file> → YYYY/MM/DD/<file> ---
    m = re.match(r"^analyses/(\d{4}/\d{2}/\d{2}/.+)$", old_path)
    if m:
        return m.group(1)

    # analyses/.gitkeep → delete
    if old_path == "analyses/.gitkeep":
        return None

    # --- syntheses (multiple formats) ---
    # syntheses/YYYY/MM/DD-<rest>.md → YYYY/MM/DD/<rest-as-synthesis>.md
    m = re.match(r"^syntheses/(\d{4})/(\d{2})/(\d{2})-(.+)$", old_path)
    if m:
        y, mo, d, rest = m.groups()
        # Classify: blog-* stays as blog-synthesis-*, else tweet-synthesis-*
        if rest.startswith("blog-"):
            new_name = f"blog-synthesis-{rest[5:]}"  # strip "blog-" prefix, add "blog-synthesis-"
        else:
            new_name = f"tweet-synthesis-{rest}"
        return f"{y}/{mo}/{d}/{new_name}"

    # syntheses/YYYY-MM-DD-blog-<rest>.md (newer format)
    m = re.match(r"^syntheses/(\d{4})-(\d{2})-(\d{2})-blog-(.+)$", old_path)
    if m:
        y, mo, d, rest = m.groups()
        return f"{y}/{mo}/{d}/blog-synthesis-{rest}"

    # syntheses/.gitkeep → delete
    if old_path == "syntheses/.gitkeep":
        return None

    # --- logs ---
    # logs/blog/YYYY/MM/DD-<rest>.md → YYYY/MM/DD/run-log-blog-<rest>.md
    m = re.match(r"^(?:kb/)?logs/blog/(\d{4})/(\d{2})/(\d{2})-(.+)$", old_path)
    if m:
        y, mo, d, rest = m.groups()
        return f"{y}/{mo}/{d}/run-log-blog-{rest}"

    # --- meta/ → _system/meta/ ---
    m = re.match(r"^meta/(.+)$", old_path)
    if m:
        return f"_system/meta/{m.group(1)}"

    # --- profile/ → _system/profile/ ---
    m = re.match(r"^profile/(.+)$", old_path)
    if m:
        return f"_system/profile/{m.group(1)}"

    # --- seed/ → _system/seed/ ---
    m = re.match(r"^seed/(.+)$", old_path)
    if m:
        return f"_system/seed/{m.group(1)}"

    # --- index.md, index-blog.md → delete (replaced by per-date READMEs) ---
    if old_path in ("index.md", "index-blog.md"):
        return None

    # --- Everything else stays (README.md, .gitignore, .github/*, scripts/*, topics/*) ---
    return old_path


def main():
    # 1. Get current commit SHA on main
    print("Fetching current main ref...")
    ref = api("get", "/git/ref/heads/main")
    commit_sha = ref["object"]["sha"]
    print(f"  commit: {commit_sha[:7]}")

    # 2. Get the full tree recursively
    print("Fetching full tree...")
    tree_resp = api("get", f"/git/trees/{commit_sha}?recursive=1")
    old_entries = tree_resp["tree"]
    print(f"  {len(old_entries)} entries (blobs + trees)")

    # 3. Remap paths — only blobs (files), trees are auto-derived
    new_entries = []
    moves = 0
    deletes = 0
    unchanged = 0

    for entry in old_entries:
        if entry["type"] != "blob":
            continue  # skip tree entries — GitHub derives them from blob paths

        old_path = entry["path"]
        new_path = remap_path(old_path)

        if new_path is None:
            deletes += 1
            continue  # file deleted
        elif new_path != old_path:
            moves += 1
        else:
            unchanged += 1

        new_entries.append({
            "path": new_path,
            "mode": entry["mode"],
            "type": "blob",
            "sha": entry["sha"],
        })

    print(f"\nMigration plan:")
    print(f"  Moves:     {moves}")
    print(f"  Deletes:   {deletes}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Total new: {len(new_entries)}")

    # 4. Create the new tree
    print("\nCreating new tree...")
    new_tree = api("post", "/git/trees", json={"tree": new_entries})
    new_tree_sha = new_tree["sha"]
    print(f"  tree: {new_tree_sha[:7]}")

    # 5. Create a commit
    print("Creating commit...")
    new_commit = api("post", "/git/commits", json={
        "message": "restructure: date-first layout (YYYY/MM/DD/ at top level)\n\n"
                   f"Moved {moves} files, deleted {deletes} stale entries.\n"
                   "analyses/ → YYYY/MM/DD/\n"
                   "syntheses/ → YYYY/MM/DD/*-synthesis-*\n"
                   "logs/ → YYYY/MM/DD/run-log-*\n"
                   "meta/, profile/, seed/ → _system/\n"
                   "Deleted: index.md, index-blog.md, .gitkeep files",
        "tree": new_tree_sha,
        "parents": [commit_sha],
    })
    new_commit_sha = new_commit["sha"]
    print(f"  commit: {new_commit_sha[:7]}")

    # 6. Update main to point to the new commit
    print("Updating main ref...")
    api("patch", "/git/refs/heads/main", json={"sha": new_commit_sha})
    print(f"\n✅ Migration complete: {new_commit_sha[:7]}")
    print(f"   {moves} files moved, {deletes} deleted, {unchanged} unchanged")


if __name__ == "__main__":
    main()
