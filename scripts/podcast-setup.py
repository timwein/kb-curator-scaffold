#!/usr/bin/env python3
"""
ONE-TIME SETUP for kb-podcast-curator.

This is a sibling of setup.py (the blog agent). The podcast agent shares the
same seed files (interest profile, taxonomy, url_sources, etc.) as the blog
agent — so this script REUSES the blog agent's SEED_FILE_IDS instead of
re-uploading. Run setup.py FIRST if .env doesn't have SEED_FILE_IDS yet.

Run this once locally. It:
  1. Verifies SEED_FILE_IDS exists in .env (fails loudly if missing)
  2. Creates a cloud environment for the podcast agent → persists PODCAST_ENV_ID
  3. Creates the agent with the system prompt from kb-podcast-curator.system.md
     → persists PODCAST_AGENT_ID, PODCAST_AGENT_VERSION
  4. Writes podcast-specific keys to .env (never clobbers blog keys)

To UPDATE the agent later (tweak the system prompt), run with `--update`.
That calls agents.update() on PODCAST_AGENT_ID, which bumps the version.
Never re-create the agent — versioning is load-bearing.

Usage:
    python podcast-setup.py          # first-time setup
    python podcast-setup.py --update # update existing agent with new system prompt
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import anthropic
from dotenv import dotenv_values, set_key

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENV_FILE = PROJECT_ROOT / ".env"
SYSTEM_PROMPT_FILE = PROJECT_ROOT / "agents" / "kb-podcast-curator.system.md"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}


def save_env_key(key: str, value: str) -> None:
    set_key(str(ENV_FILE), key, value, quote_mode="never")


def require_seed_file_ids(env: dict[str, str]) -> str:
    """The podcast agent reuses the blog agent's seed file uploads. Fail loudly
    if setup.py (the blog agent's) hasn't been run yet."""
    seed_ids = env.get("SEED_FILE_IDS")
    if not seed_ids:
        raise SystemExit(
            "SEED_FILE_IDS not found in .env.\n"
            "\n"
            "The podcast agent reuses seed files uploaded by the blog agent.\n"
            "Run the blog setup first:  python setup.py\n"
            "\n"
            "That uploads subscriptions.md, topic_taxonomy.md, url_sources.json,\n"
            "and the other seed files, then persists SEED_FILE_IDS here. Once\n"
            "that's done, re-run this script."
        )
    return seed_ids


def create_environment(client: anthropic.Anthropic) -> str:
    env = client.beta.environments.create(
        name="kb-podcast-curator-env",
        config={
            "type": "cloud",
            "networking": {"type": "unrestricted"},
        },
    )
    return env.id


def create_agent(client: anthropic.Anthropic, system_prompt: str) -> tuple[str, str]:
    agent = client.beta.agents.create(
        name="kb-podcast-curator",
        description="Scheduled curator for podcast interview transcripts. Writes structured analyses to <your-username>/<your-kb-repo> alongside the blog and tweet agents.",
        model="claude-opus-4-6",
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
    """Update the existing agent. Returns the new version string."""
    updated = client.beta.agents.update(
        agent_id,
        version=int(current_version),
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




# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="Update the existing podcast agent with the current system prompt (bumps version)")
    args = parser.parse_args()

    if not SYSTEM_PROMPT_FILE.exists():
        raise SystemExit(f"System prompt not found: {SYSTEM_PROMPT_FILE}")
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")

    client = anthropic.Anthropic()
    env = load_env()

    if args.update:
        agent_id = env.get("PODCAST_AGENT_ID")
        current_version = env.get("PODCAST_AGENT_VERSION")
        if not agent_id or not current_version:
            raise SystemExit("PODCAST_AGENT_ID/PODCAST_AGENT_VERSION not in .env — run without --update first.")
        print(f"Updating podcast agent {agent_id} (from version {current_version})...")
        new_version = update_agent(client, agent_id, system_prompt, current_version)
        save_env_key("PODCAST_AGENT_VERSION", new_version)
        print(f"✓ Podcast agent updated to version {new_version}")
        return

    # -------- Fresh setup --------
    print("=" * 60)
    print("kb-podcast-curator — one-time setup")
    print("=" * 60)

    # 1. Verify seed files already uploaded by blog setup
    print("\n[1/3] Verifying SEED_FILE_IDS from blog agent...")
    seed_ids = require_seed_file_ids(env)
    n_seeds = len([p for p in seed_ids.split(",") if ":" in p])
    print(f"    → reusing {n_seeds} seed file IDs (shared with blog agent)")

    # 2. Environment
    if "PODCAST_ENV_ID" not in env:
        print("\n[2/3] Creating podcast environment...")
        env_id = create_environment(client)
        save_env_key("PODCAST_ENV_ID", env_id)
        print(f"    → {env_id}")
    else:
        print(f"\n[2/3] Podcast environment already created: {env['PODCAST_ENV_ID']}")

    # 3. Agent
    if "PODCAST_AGENT_ID" not in env:
        print("\n[3/3] Creating podcast agent...")
        agent_id, version = create_agent(client, system_prompt)
        save_env_key("PODCAST_AGENT_ID", agent_id)
        save_env_key("PODCAST_AGENT_VERSION", version)
        print(f"    → {agent_id} (version {version})")
    else:
        print(f"\n[3/3] Podcast agent already created: {env['PODCAST_AGENT_ID']}")

    print("\n" + "=" * 60)
    print("Setup complete. New IDs saved to .env:")
    print("=" * 60)
    final_env = load_env()
    for key in ("PODCAST_ENV_ID", "PODCAST_AGENT_ID", "PODCAST_AGENT_VERSION"):
        if key in final_env:
            val = final_env[key]
            display = val if len(val) < 80 else val[:77] + "..."
            print(f"  {key}={display}")

    print("\nBlog agent keys (unchanged):")
    for key in ("ENV_ID", "AGENT_ID", "AGENT_VERSION"):
        if key in final_env:
            val = final_env[key]
            display = val if len(val) < 80 else val[:77] + "..."
            print(f"  {key}={display}")

    print("\nNext steps:")
    print("  1. Add these three values to the KB repo's GitHub Actions secrets:")
    print("     PODCAST_ENV_ID, PODCAST_AGENT_ID, PODCAST_AGENT_VERSION")
    print("  2. Copy podcast-run.py to KB repo at scripts/run-podcast-ingest.py")
    print("  3. Copy podcast-ingest.yml to KB repo at .github/workflows/podcast-ingest.yml")
    print("  4. Update cron-watchdog.yml in the KB repo (covers both workflows now)")
    print("  5. Manual-dispatch the first podcast run from the Actions tab.")


if __name__ == "__main__":
    main()
