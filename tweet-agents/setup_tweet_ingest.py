"""
ONE-TIME SETUP — creates (or updates) the Managed Agents environment and agent.

Run this:
  - Once during initial setup, after you have ANTHROPIC_API_KEY exported
  - Any time you've edited the system prompt in lib/prompts.py and want the
    change to take effect (it updates the agent in place, creating a new
    version)

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python setup.py

Writes the environment_id, agent_id, and agent_version into config.json.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic

from lib.prompts import SYSTEM_PROMPT


CONFIG_PATH = Path(__file__).parent / "config.json"
EXAMPLE_PATH = Path(__file__).parent / "config.example.json"

ENVIRONMENT_NAME = "tweet-kb-environment"
AGENT_NAME = "tweet-kb-agent"
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
    existing_agent_id = config.get("agent_id")
    is_placeholder = (
        not existing_agent_id
        or existing_agent_id.startswith("agent_REPLACE")
    )

    agent_tools = [{"type": "agent_toolset_20260401"}]

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
        existing_version = int(config.get("agent_version") or 1)
        print(f"Agent '{AGENT_NAME}': updating {existing_agent_id} (v{existing_version})...", flush=True)
        agent = client.beta.agents.update(
            agent_id=existing_agent_id,
            version=existing_version,
            name=AGENT_NAME,
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=agent_tools,
        )
        print(f"  updated to version {agent.version}")

    # ---- 3. Persist IDs --------------------------------------------------
    config["environment_id"] = environment.id
    config["agent_id"] = agent.id
    config["agent_version"] = (
        str(agent.version) if getattr(agent, "version", None) is not None else None
    )
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")

    print()
    print(f"Wrote IDs to {CONFIG_PATH}")
    print("Next steps:")
    print("  1. Ensure GITHUB_PAT is exported (fine-grained PAT for your KB repo)")
    print("  2. Run: python -m lib.fetcher --login   (one-time X login)")
    print("  3. Run: python run.py                   (first ingestion)")


if __name__ == "__main__":
    main()
