"""
One-time helper: generates a launchd plist that runs run.py at 6am, noon,
and 6pm local time (PT).

Usage:
    python make_launchd.py

Writes ~/Library/LaunchAgents/com.example.tweet-kb-agent.plist
Then run:
    launchctl load ~/Library/LaunchAgents/com.example.tweet-kb-agent.plist
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PLIST_PATH = Path.home() / "Library/LaunchAgents/com.example.tweet-kb-agent.plist"
PROJECT_DIR = Path(__file__).parent.resolve()
PYTHON = PROJECT_DIR / ".venv/bin/python"
LOG = Path("/tmp/tweet-kb-agent.log")

REQUIRED_VARS = ["ANTHROPIC_API_KEY", "GITHUB_PAT"]


def main() -> None:
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        sys.exit(
            f"ERROR: missing env vars: {', '.join(missing)}\n"
            "Export them first, then re-run this script."
        )

    env_xml = "\n".join(
        f"        <key>{v}</key>\n        <string>{os.environ[v]}</string>"
        for v in REQUIRED_VARS
    )

    plist = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.tweet-kb-agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON}</string>
        <string>{PROJECT_DIR}/run.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{PROJECT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
{env_xml}
    </dict>

    <!-- 6 am, noon, 6 pm local time -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key><integer>6</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>12</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>18</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>{LOG}</string>
    <key>StandardErrorPath</key>
    <string>{LOG}</string>

    <!-- Re-run missed firings (e.g. if laptop was asleep) -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)
    print(f"Wrote {PLIST_PATH}")
    print()
    print("Load it now:")
    print(f"  launchctl load {PLIST_PATH}")
    print()
    print("To unload later:")
    print(f"  launchctl unload {PLIST_PATH}")
    print()
    print("Logs will go to /tmp/tweet-kb-agent.log")


if __name__ == "__main__":
    main()
