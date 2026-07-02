#!/usr/bin/env python3
"""
ONE-TIME SETUP for kb-blog-curator.

Run this once locally. It:
  1. Uploads the six seed files via the Files API → persists file_ids
  2. Creates a cloud environment → persists env_id
  3. Creates the agent with the system prompt from kb-blog-curator.system.md → persists agent_id
  4. Writes everything to .env so run.py (and GitHub Actions) can read them back

To UPDATE the agent later (tweak the system prompt, add a tool), run this script
with `--update`. It will call agents.update() on the existing agent_id, which
bumps the version. Never re-create the agent — versioning is load-bearing.

Usage:
    python setup.py          # first-time setup
    python setup.py --update # update existing agent with new system prompt
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import anthropic
from dotenv import dotenv_values, set_key

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENV_FILE = PROJECT_ROOT / ".env"
SYSTEM_PROMPT_FILE = PROJECT_ROOT / "agents" / "kb-blog-curator.system.md"

MODEL = "claude-sonnet-4-6"

# The authoritative seed files live in the lowercase folder — topic_taxonomy.md
# and url_sources.py only exist there, so use it as canonical.
SEED_DIR = Path(os.environ.get("SEED_DIR", str(PROJECT_ROOT / "seed")))

SEED_FILES = [
    ("subscriptions.md",         "text/markdown"),   # PRIMARY — current newsletter subs, topic-tagged
    ("interests_seed.md",        "text/markdown"),
    ("topic_taxonomy.md",        "text/markdown"),
]

# Optional taste-profile artifacts. These deepen personalization but aren't
# required to get running — generate them later with
# tweet-agents/seed_from_claude_export.py and tweet-agents/build_taste_profile.py.
OPTIONAL_SEED_FILES = [
    ("url_sources.json",         "application/json"),
    ("url_sources.md",           "text/markdown"),
    ("claude_messages_clean.md", "text/markdown"),
    ("url_sources.py",           "text/x-python"),
]

# Canonical mount paths inside the session container.
MOUNT_ROOT = "/workspace/seed"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}


def save_env_key(key: str, value: str) -> None:
    set_key(str(ENV_FILE), key, value, quote_mode="never")


def upload_seed_files(client: anthropic.Anthropic) -> dict[str, str]:
    """Upload each seed file; return {filename: file_id}."""
    file_ids: dict[str, str] = {}
    for name, _ in SEED_FILES:
        path = SEED_DIR / name
        if not path.exists():
            raise FileNotFoundError(
                f"Seed file missing: {path}\n"
                "Copy seed-templates/ to seed/, drop the EXAMPLE_ prefixes, and personalize."
            )
        print(f"  uploading {name} ({path.stat().st_size:,} bytes)...", flush=True)
        with path.open("rb") as f:
            uploaded = client.beta.files.upload(file=f)
        print(f"    → {uploaded.id}")
        file_ids[name] = uploaded.id
    for name, _ in OPTIONAL_SEED_FILES:
        path = SEED_DIR / name
        if name == "url_sources.py" and not path.exists():
            # The canonical URL-cleaning module ships with the scaffold.
            path = PROJECT_ROOT / "scripts" / "url_sources.py"
        if not path.exists():
            print(f"  (skipping optional seed file {name} — not found in {SEED_DIR})")
            continue
        print(f"  uploading {name} ({path.stat().st_size:,} bytes)...", flush=True)
        with path.open("rb") as f:
            uploaded = client.beta.files.upload(file=f)
        print(f"    → {uploaded.id}")
        file_ids[name] = uploaded.id
    return file_ids


def create_environment(client: anthropic.Anthropic) -> str:
    env = client.beta.environments.create(
        name="kb-blog-curator-env",
        config={
            "type": "cloud",
            "networking": {"type": "unrestricted"},
        },
    )
    return env.id


def create_agent(client: anthropic.Anthropic, system_prompt: str) -> tuple[str, str]:
    agent = client.beta.agents.create(
        name="kb-blog-curator",
        description="Scheduled curator for long-form blog and Substack content. Writes structured analyses to <your-username>/<your-kb-repo>.",
        model=MODEL,
        system=system_prompt,
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    return agent.id, str(agent.version)


def update_agent(client: anthropic.Anthropic, agent_id: str, system_prompt: str, current_version: str) -> str:
    """Update the existing agent. Returns the new version string.

    `current_version` is the version we're updating FROM — required by the API
    as an optimistic-concurrency check.
    """
    updated = client.beta.agents.update(
        agent_id,
        version=int(current_version),
        model=MODEL,
        system=system_prompt,
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    return str(updated.version)


def sync_github_agent_version_secret(new_version: str, kb_repo_url: str | None) -> None:
    """Best-effort: push AGENT_VERSION=<new_version> to the KB repo's Actions secrets.

    Uses the `gh` CLI (must be on PATH) and GITHUB_PAT / GH_TOKEN from env.
    On any failure, prints manual-fix instructions and returns — never raises.
    """
    if not kb_repo_url:
        print("  (skip GH secret sync: KB_REPO_URL not set in .env)")
        return

    # Parse "https://github.com/owner/repo" or "https://github.com/owner/repo.git"
    parsed = urlparse(kb_repo_url)
    path = parsed.path.strip("/").removesuffix(".git")
    if parsed.netloc != "github.com" or path.count("/") != 1:
        print(f"  (skip GH secret sync: can't parse owner/repo from KB_REPO_URL={kb_repo_url})")
        return
    repo = path  # "owner/repo"

    if not shutil.which("gh"):
        print("  (skip GH secret sync: `gh` CLI not installed — run: "
              f"gh secret set AGENT_VERSION --repo {repo} --body {new_version})")
        return

    token = os.environ.get("GITHUB_PAT") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("  (skip GH secret sync: no GITHUB_PAT/GH_TOKEN in env — run: "
              f"gh secret set AGENT_VERSION --repo {repo} --body {new_version})")
        return

    try:
        subprocess.run(
            ["gh", "secret", "set", "AGENT_VERSION", "--repo", repo, "--body", new_version],
            env={**os.environ, "GH_TOKEN": token},
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  → synced AGENT_VERSION={new_version} to GH secrets on {repo}")
    except subprocess.CalledProcessError as e:
        print(f"  ! GH secret sync failed (exit {e.returncode}): {e.stderr.strip() or e.stdout.strip()}")
        print(f"    Fix manually: gh secret set AGENT_VERSION --repo {repo} --body {new_version}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="Update the existing agent with the current system prompt (bumps version)")
    args = parser.parse_args()

    if not SYSTEM_PROMPT_FILE.exists():
        raise SystemExit(f"System prompt not found: {SYSTEM_PROMPT_FILE}")
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")

    client = anthropic.Anthropic()
    env = load_env()

    if args.update:
        agent_id = env.get("AGENT_ID")
        current_version = env.get("AGENT_VERSION")
        if not agent_id or not current_version:
            raise SystemExit("AGENT_ID/AGENT_VERSION not in .env — run without --update first.")
        print(f"Updating agent {agent_id} (from version {current_version})...")
        new_version = update_agent(client, agent_id, system_prompt, current_version)
        save_env_key("AGENT_VERSION", new_version)
        print(f"✓ Agent updated to version {new_version}")
        print("Syncing AGENT_VERSION to GitHub Actions secrets...")
        sync_github_agent_version_secret(new_version, env.get("KB_REPO_URL"))
        return

    # -------- Fresh setup --------
    print("=" * 60)
    print("kb-blog-curator — one-time setup")
    print("=" * 60)

    # 1. Seed files
    if "SEED_FILE_IDS" not in env:
        print("\n[1/3] Uploading seed files...")
        file_ids = upload_seed_files(client)
        seed_file_ids_str = ",".join(f"{name}:{fid}" for name, fid in file_ids.items())
        save_env_key("SEED_FILE_IDS", seed_file_ids_str)
    else:
        print("\n[1/3] Seed files already uploaded (SEED_FILE_IDS in .env). "
              "Delete the line to re-upload.")

    # 2. Environment
    if "ENV_ID" not in env:
        print("\n[2/3] Creating environment...")
        env_id = create_environment(client)
        save_env_key("ENV_ID", env_id)
        print(f"    → {env_id}")
    else:
        print(f"\n[2/3] Environment already created: {env['ENV_ID']}")

    # 3. Agent
    if "AGENT_ID" not in env:
        print("\n[3/3] Creating agent...")
        agent_id, version = create_agent(client, system_prompt)
        save_env_key("AGENT_ID", agent_id)
        save_env_key("AGENT_VERSION", version)
        print(f"    → {agent_id} (version {version})")
    else:
        print(f"\n[3/3] Agent already created: {env['AGENT_ID']}")

    print("\n" + "=" * 60)
    print("Setup complete. IDs saved to .env:")
    print("=" * 60)
    final_env = load_env()
    for key in ("SEED_FILE_IDS", "ENV_ID", "AGENT_ID", "AGENT_VERSION"):
        if key in final_env:
            # Truncate long values for display
            val = final_env[key]
            display = val if len(val) < 80 else val[:77] + "..."
            print(f"  {key}={display}")

    print("\nNext steps:")
    print("  1. Copy .env values into GitHub Actions secrets on the KB repo:")
    print("     ANTHROPIC_API_KEY, ENV_ID, AGENT_ID, AGENT_VERSION, SEED_FILE_IDS")
    print("  2. Copy run.py to kb repo at scripts/run-blog-ingest.py")
    print("  3. Copy blog-ingest.yml to kb repo at .github/workflows/blog-ingest.yml")
    print("  4. Push the KB repo. First scheduled run will bootstrap kb/profile/")


if __name__ == "__main__":
    main()
