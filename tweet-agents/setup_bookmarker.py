"""
ONE-TIME SETUP for the X bookmarking agent (agent #3).

Creates/updates:
  - A NEW CMA environment ("tweet-bookmark-environment").
  - A NEW CMA agent ("tweet-bookmark-agent") with the bookmark_tweet
    custom tool declared.

Writes the new IDs into config.json under:
  - bookmark_environment_id
  - bookmark_agent_id
  - bookmark_agent_version

Does NOT touch the existing ingestion-agent IDs (environment_id,
agent_id, agent_version). Safe to re-run — idempotent, updates the
agent in place when lib/bookmark_prompts.py changes.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python setup_bookmarker.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic

from lib.bookmark_prompts import BOOKMARK_TOOL, SYSTEM_PROMPT


CONFIG_PATH = Path(__file__).parent / "config.json"
EXAMPLE_PATH = Path(__file__).parent / "config.example.json"

ENVIRONMENT_NAME = "tweet-bookmark-environment"
AGENT_NAME = "tweet-bookmark-agent"
MODEL = "claude-opus-4-6"


def load_or_init_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    if not EXAMPLE_PATH.exists():
        sys.exit("config.example.json is missing. Re-check the install.")
    return json.loads(EXAMPLE_PATH.read_text())


def find_environment_by_name(client: anthropic.Anthropic, name: str):
    """Page through environments to find one matching `name`, or None."""
    for env in client.beta.environments.list():
        if getattr(env, "name", None) == name:
            return env
    return None


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set in the environment.")

    config = load_or_init_config()
    client = anthropic.Anthropic()

    # ---- 1. Environment (create or reuse) --------------------------------
    print(f"Environment '{ENVIRONMENT_NAME}': ", end="", flush=True)
    existing_env = find_environment_by_name(client, ENVIRONMENT_NAME)
    if existing_env is not None:
        environment = existing_env
        print(f"reusing {environment.id}")
    else:
        environment = client.beta.environments.create(
            name=ENVIRONMENT_NAME,
            config={
                "type": "cloud",
                "networking": {"type": "unrestricted"},
            },
        )
        print(f"created {environment.id}")

    # ---- 2. Agent (create or update in place) ----------------------------
    existing_agent_id = config.get("bookmark_agent_id")
    is_placeholder = (
        not existing_agent_id
        or str(existing_agent_id).startswith("agent_REPLACE")
    )

    # Built-in toolset (read, grep, glob for KB reading) + the custom
    # bookmark_tweet tool. No bash/write needed — this agent only reads
    # and judges.
    agent_tools = [
        {
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                # This agent should NEVER write or run commands in the
                # container. Disable write/edit/bash to prevent accidents.
                {"name": "write", "enabled": False},
                {"name": "edit", "enabled": False},
                {"name": "bash", "enabled": False},
            ],
        },
        BOOKMARK_TOOL,
    ]

    if is_placeholder:
        print(f"Agent '{AGENT_NAME}': creating...", flush=True)
        agent = client.beta.agents.create(
            name=AGENT_NAME,
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=agent_tools,
        )
        print(f"  created {agent.id} (version {agent.version})")
    else:
        existing_version = int(config.get("bookmark_agent_version") or 1)
        print(
            f"Agent '{AGENT_NAME}': updating {existing_agent_id} (v{existing_version})...",
            flush=True,
        )
        agent = client.beta.agents.update(
            agent_id=existing_agent_id,
            version=existing_version,
            name=AGENT_NAME,
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=agent_tools,
        )
        print(f"  updated to version {agent.version}")

    # ---- 3. Persist IDs (without touching ingestion-agent keys) ----------
    config["bookmark_environment_id"] = environment.id
    config["bookmark_agent_id"] = agent.id
    config["bookmark_agent_version"] = (
        str(agent.version) if getattr(agent, "version", None) is not None else None
    )
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")

    print()
    print(f"Wrote bookmark-agent IDs to {CONFIG_PATH}")
    print("Next steps:")
    print("  1. python build_taste_profile.py   # generates the taste profile in the KB")
    print("  2. python run_bookmarker.py        # first hourly run (manual)")
    print("  3. Install the launchd plist for hourly automation")


if __name__ == "__main__":
    main()
