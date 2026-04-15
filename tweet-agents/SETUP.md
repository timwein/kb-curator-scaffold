# Tweet Agents Setup

This directory contains the two tweet-side managed agents:

1. **`tweet-kb-agent`** (run via `run_tweet_ingest.py`) — reads your X
   bookmarks 3×/day, writes structured analyses + a synthesis into the KB.
2. **`tweet-bookmarker`** (run via `run_bookmarker.py`) — runs hourly from
   6am–midnight, scrapes your X feeds, judges candidates against your
   taste profile, and places bookmarks. Those bookmarks flow into
   `tweet-kb-agent` on its next run — the loop is closed.

Both use a Claude Managed Agents session for the reasoning step. The
bookmarker does the actual bookmark click locally (Playwright) because X's
authenticated write path needs cookies bound to your real Chrome session.

## Architecture

- **Local orchestrators** — run on your Mac. `run_tweet_ingest.py` fetches
  bookmarks via a persistent-profile Playwright Chromium, then starts a
  Managed Agent session with the KB repo mounted. `run_bookmarker.py`
  scrapes feeds + receives `bookmark_tweet` custom-tool calls from the
  bookmark agent and executes the bookmark click locally.
- **Managed Agents** — Anthropic-hosted. `tweet-kb-agent` has `bash`,
  `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search` and
  does the analysis + commit + push. `tweet-bookmark-agent` has
  `web_fetch` + the `bookmark_tweet` custom tool (host-side execution).
- **KB repo** — your `<your-username>/<your-kb-repo>`. The repo is the
  synthesis destination — read it via GitHub, the GitHub mobile app, or
  the Next.js `reader/` in the parent scaffold.

## One-time setup

### 1. Install dependencies

```bash
cd <path-to-this-checkout>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Create a GitHub fine-grained PAT

1. Go to <https://github.com/settings/personal-access-tokens/new>
2. **Token name:** `tweet-kb-agent`
3. **Resource owner:** your account
4. **Expiration:** 1 year (set a calendar reminder to rotate)
5. **Repository access:** "Only select repositories" → `<your-username>/<your-kb-repo>`
6. **Permissions → Repository permissions:**
   - **Contents: Read and write**
   - (Everything else: default / None)
7. Click Generate, copy the token.

### 3. Export credentials

Add to your shell profile (`~/.zshrc`):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_PAT="github_pat_..."
```

Then `source ~/.zshrc` (or open a new terminal).

### 4. Initialize config

```bash
cp config.example.json config.json
# Leave the "REPLACE_ME" IDs for now; the setup scripts below fill them in.
# Edit `github_repo_url` in config.json to point at your own KB repo.
```

### 5. Verify your X session is readable

The fetcher reads X cookies directly from your regular Chrome (via `browser_cookie3`) — no separate login step needed. Just make sure you're logged in to x.com in Chrome. Then verify the cookie import works:

```bash
python -m lib.fetcher --diagnose
```

You should see `auth_token present — session looks good.` If not, open Chrome, log in to x.com, reload your bookmarks page once, and retry.

### 6. Create the Managed Agent environments + agents

```bash
python setup_tweet_ingest.py   # creates the tweet-kb-agent (analysis + commit)
python setup_bookmarker.py     # creates the tweet-bookmark-agent (feed scoring)
```

Each writes its `environment_id`, `agent_id`, and `agent_version` into
`config.json` under distinct keys (see `config.example.json`).

### 7. First runs (manual)

```bash
python run_tweet_ingest.py     # analyze current bookmarks + push
python run_bookmarker.py       # score feeds + place bookmarks
```

Both stream event output to the terminal. On success they end with a `git
push` to the KB repo.

## Seeding from Claude.ai export (one-time, optional)

Once you have your `conversations.json` from claude.ai's data export:

```bash
# Clone your KB repo locally for the seeding step:
git clone https://github.com/<your-username>/<your-kb-repo> ~/tmp/kb-clone

# Dry run first — see what would happen:
python seed_from_claude_export.py /path/to/conversations.json \
    --kb-path ~/tmp/kb-clone --dry-run

# Looks right? Drop --dry-run:
python seed_from_claude_export.py /path/to/conversations.json \
    --kb-path ~/tmp/kb-clone

# Commit and push:
cd ~/tmp/kb-clone
git add _system/seed/ _system/meta/seeded.jsonl
git commit -m "seed KB from claude.ai export"
git push
```

Subsequent CMA runs automatically search `_system/seed/` alongside the
date folders for context.

## Automating the schedules

**Tweet ingestion (3×/day).** Either cron or launchd works. For launchd,
run:

```bash
python make_launchd.py
launchctl load ~/Library/LaunchAgents/com.example.tweet-kb-agent.plist
```

`make_launchd.py` reads your exported `ANTHROPIC_API_KEY` and `GITHUB_PAT`
and writes a filled-in plist into `~/Library/LaunchAgents/`. **Edit the
label** inside the script (`com.example.tweet-kb-agent`) to something
under your own namespace before running.

**Bookmarker (hourly, 6am–midnight).** Use the
`com.example.tweet-bookmarker.plist.template` in this directory as a
starting point. Copy it to `~/Library/LaunchAgents/` under your own
label, fill in the paths and secrets, then `launchctl load` it. The
template fires 19 times per day (hours 0, 6, 7, …, 23) and keeps an
overnight quiet window.

**NEVER commit a filled-in plist — it contains your API keys.** Keep
plists out of version control (the scaffold's `.gitignore` already
excludes them).

## Iteration / updating

**To tweak an agent's behavior:** edit its system prompt (`lib/prompts.py`
for tweet ingestion, `lib/bookmark_prompts.py` for the bookmarker), then
re-run the matching `setup_*.py`. It updates the existing agent in place,
creating a new version. The next `run_*.py` invocation picks it up
automatically.

**To increase or decrease bookmark cap per run:** edit
`bookmark_candidates_per_run` (for candidate sweep size) or the agent's
`max_bookmarks_per_run` tool-result cap.

## Troubleshooting

**"Bookmarks page did not load any tweets"** — your X session expired.
Open Chrome, log in, reload x.com/i/bookmarks once, retry.

**No bookmarks scraped but you know you have some** — X may have changed
its DOM. Open `lib/fetcher.py` and inspect the selectors at the top. Run
`python -m lib.fetcher --no-headless` to see what the browser is doing.

**Push fails with "non-fast-forward"** — someone else (or another run)
pushed to the repo since this session cloned it. The agent's system
prompt instructs it to `git pull --rebase` and retry, but if that fails,
you can resolve manually in a local clone.

**Session hangs at `[thinking...]`** — this is normal; adaptive thinking
can run for a while on complex analyses. Look for `agent.message` events
to confirm progress.

**"config.json has placeholder IDs"** — you haven't run the matching
`setup_*.py` yet, or it errored out. Run it again.
