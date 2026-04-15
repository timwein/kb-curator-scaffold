# Personal Knowledge Base — Self-Curating with Claude Managed Agents

A knowledge base that **curates itself**. Two AI agents run on a schedule, pull new content matching your interests, write structured analyses, and commit everything to a date-organized GitHub repo. You give 0-10 ratings to what you read; the agents learn and improve over time.

This is what powers a researcher, 's daily reading workflow — but the architecture works for any researcher, analyst, or domain expert who reads widely and wants AI to do the synthesis work.

---

## What you get

Every morning, midday, and evening, scheduled cron jobs trigger two agents on Anthropic's Managed Agents infrastructure:

- **`tweet-kb-agent`** — ingests your bookmarked tweets, writes a structured analysis per tweet, cross-references existing knowledge, and produces a per-run synthesis you can read on your phone.
- **`kb-blog-curator`** — monitors ~150 blogs/Substacks you subscribe to, hunts for new sources via web search, ranks candidates by relevance to your interests, picks the strongest, writes deep analyses, and commits them to the same KB.

Both agents share the same GitHub repo (`<your-kb-repo>` in the original setup). The repo is organized **date-first** — you navigate `2026/04/15/` and see everything from that day side-by-side: blog analyses, tweet analyses, syntheses, and a daily README landing page.

The KB grows by accretion. Topics files cross-link tweet content with blog content. Over time, the agents discover new sources beyond your seed list. You give feedback by rating analyses (`user_score: 0-10`) — the agents adjust their interest model accordingly.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Actions (cron 3x daily + watchdog hourly)               │
│    • blog-ingest.yml        → kicks off blog curator            │
│    • cron-watchdog.yml      → catches missed runs               │
│    • (your bookmark workflow) → kicks off tweet agent           │
└───────────────────┬─────────────────────────────────────────────┘
                    │  POST /v1/sessions
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Anthropic Managed Agents (cloud-hosted)                        │
│                                                                  │
│  ┌─────────────────────┐         ┌──────────────────────┐       │
│  │ tweet-kb-agent      │         │ kb-blog-curator      │       │
│  │ (system prompt v4+) │         │ (system prompt v11+) │       │
│  └────────┬────────────┘         └──────────┬───────────┘       │
│           │ runs in sandboxed container      │                  │
│           │ • git clone repo                 │                  │
│           │ • read seeds                     │                  │
│           │ • analyze + commit + push        │                  │
│           │ • stop                           │                  │
└───────────┼──────────────────────────────────┼──────────────────┘
            │                                  │
            └──────────────┬───────────────────┘
                           │  git push
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Repo: <your>-knowledge-base (private)                   │
│                                                                  │
│  2026/                                                           │
│    └── 04/                                                       │
│        └── 15/                                                   │
│            ├── README.md                    ← daily landing page │
│            ├── blog-<pub>-<slug>.md         ← blog analyses      │
│            ├── blog-synthesis-morning.md    ← blog digest        │
│            ├── <tweet-id>-<author>-<slug>.md ← tweet analyses    │
│            ├── tweet-synthesis-morning.md   ← tweet digest       │
│            └── run-log-blog-morning.md      ← exhaustive audit   │
│  topics/                                                         │
│    └── <topic-slug>.md                      ← cross-cutting refs │
│  _system/                                                        │
│    ├── profile/                             ← agent's evolving   │
│    │   ├── deltas.md                          interest model     │
│    │   ├── feedback.md                                            │
│    │   ├── feed_map.json                    ← RSS cache          │
│    │   └── discovered_sources.md                                  │
│    ├── meta/                                                      │
│    │   ├── ingested.jsonl                   ← tweet dedupe       │
│    │   └── blogs-ingested.jsonl             ← blog dedupe        │
│    └── seed/                                ← your interest seed │
└─────────────────────────────────────────────────────────────────┘
```

The agents run on Anthropic's cloud, the cron triggers run on GitHub's cloud, and the KB lives in your GitHub repo. **Nothing runs on your laptop** — you can shut it down, the system keeps working.

---

## The agents in detail

### `tweet-kb-agent`

**Trigger:** Whenever you push a batch of bookmarked tweets to a queue file (e.g., via a Shortcut or browser extension that exports your X bookmarks). The agent processes the new tweets in the next session.

**Per-tweet output:** A markdown analysis under `YYYY/MM/DD/<tweet-id>-<author>-<slug>.md` containing:
- TLDR (2-3 sentence thesis)
- What's New / Non-Obvious
- Counterintuitive Claims
- Steelman + Steelman Rebuttal
- Forward-Looking Hypotheses
- Technical Insights, Key Assumptions, Second-Order Implications
- "My Take" — the agent's honest assessment
- 3-5 talking points for professional calls / X posts / founder chats

**Cross-references:** For each tweet, the agent searches the existing KB (topics, prior analyses, seeded conversations) and links related work. New themes spawn new topic files.

**Synthesis:** A per-run digest at `YYYY/MM/DD/tweet-synthesis-<slot>.md` — the document you read on your phone with TL;DR bullets, top analyses, surprising cross-references, and pre-distilled talking points.

### `kb-blog-curator`

**Trigger:** Cron, 3x daily (default: 7am / 12pm / 6pm Pacific). Plus manual trigger and an hourly watchdog that catches missed cron runs.

**Per-run pipeline:**

1. **Load profile** — read seed files (interest profile, topic taxonomy, subscription list, URL corpus) and the agent's evolving deltas
2. **Drain feedback inbox** — process anything you wrote to `_system/profile/feedback.md`, update interest model
3. **Passive learning** — scan recent commits for `user_score:` ratings you added; update model based on calibration gaps
4. **Discovery (two co-equal legs):**
   - **Monitor known sources** — RSS feeds for ~150 publications (Tier 1: subscriptions + manually-curated URLs + topic taxonomy URLs; Tier 2: previously-discovered sources). Uses a persistent RSS feed cache (`_system/profile/feed_map.json`) so probing is one-shot per host.
   - **Hunt for new sources** — 15-20 web_search queries per run across 5 strategies: theme-driven, author-driven, adjacent-community, "what's new" sweeps, Substack recommendation graphs
5. **Rank** — score each candidate 1-10 against the merged interest model
6. **Cap** — keep everything scoring ≥7, max 15 per run
7. **Analyze each winner** — `web_fetch` the article, write a structured analysis (same template as tweet analyses, plus a `relevance_score` and blank `user_score:` for your rating), update topic cross-references, commit and push **incrementally** (each analysis is its own atomic commit so a session crash doesn't lose work)
8. **Synthesize + index** — write the per-run digest, generate the daily landing page README

**Audit log:** Every fetch, every search query, every cache decision is logged chronologically to `YYYY/MM/DD/run-log-blog-<slot>.md`. You can `grep <publication>` to see every attempt the agent made against any source on any day.

---

## How the KB was seeded

The system isn't useful with a cold-start interest profile. It needs to know what you care about. The seeding approach:

### 1. Export your past Claude.ai conversations

If you've been having long-form conversations with Claude on a topic for months, those conversations are gold — they capture not just what you're interested in, but how your thinking has evolved. Export them via:

- The Claude.ai Chrome extension's "export all conversations" feature, or
- The Anthropic API's conversation export endpoint, or
- Manual copy-paste of the most important threads

The original setup had **451 conversations / 1,215 messages** from a 4-month window, exported as `claude_messages_clean.md` (~607KB).

### 2. Extract URLs you've referenced

Run a script over the conversation export to extract every URL you referenced. The output is a corpus of articles you found important enough to paste into a chat — a high-signal proxy for your reading interests. The original setup found **661 unique URLs across 107 publications**.

A simple Python script (`url_sources.py`) does this. It:
- Regex-matches all `https?://...` patterns
- Normalizes (strips tracking params, consolidates twitter→x, etc.)
- Classifies by source type (substack, blog, lab, arxiv, github, etc.)
- Counts how often each URL appears (`count >= 3` = high signal)
- Outputs `url_sources.json` and `url_sources.md`

### 3. Distill a topic taxonomy

Have Claude (or do it manually) read the conversation export and produce a topic taxonomy: what themes recur, what positions you've taken, how your views have evolved. The original setup organized into 23 topics across AI/frontier tech, market/macro, lifestyle clusters, with evolution flags ("Yes, view evolved sharply") and representative URLs per topic.

This becomes `topic_taxonomy.md` — the highest-signal seed file (~22KB, dense).

### 4. List your active subscriptions

Sweep your inbox (Gmail filter for newsletter/Substack senders) and produce a list of every publication actively emailing you. Tag each by topic. The original setup found **85 unique senders in 14 days**, organized by the topic taxonomy.

This becomes `subscriptions.md`.

### 5. Write a short interest profile

A 1-page "voice" document: who you are, what you do, what you read for, what you care about, with sample articles for each major theme. ~3.5KB. This becomes `interests_seed.md`.

### 6. Upload as session resources

All 5 files (interest profile, taxonomy, URL corpus, subscriptions, conversation export) are uploaded once via the Files API and mounted into every agent session at `/workspace/seed/`. The agents reference them every run — **they remain authoritative ground truth**, while learned deltas in `_system/profile/deltas.md` adjust on top.

---

## Setting it up yourself

### Prerequisites

| Item | Notes |
|---|---|
| **Anthropic API account** | Needs Managed Agents (currently in beta — apply at platform.claude.com) |
| **GitHub account + private repo** | The KB lives here. Repo can be free if you're under 1GB. |
| **GitHub fine-grained PAT** | Needs: `Contents: Read & write`, `Workflows: Read & write`, `Secrets: Read & write`, `Actions: Read & write` (all scoped to the KB repo only) |
| **Python 3.10+** | Local setup script. Once setup runs, Python isn't needed again. |
| **Your seed corpus** | Past Claude.ai exports, URL list, topic taxonomy, subscription list, interest profile |

### Step 1: Create the KB repo

```bash
gh repo create my-knowledge-base --private --clone
cd my-knowledge-base
mkdir topics _system _system/profile _system/meta _system/seed
echo "# My Knowledge Base" > README.md
git add -A && git commit -m "init" && git push -u origin main
```

### Step 2: Prepare your seed files

Put your seed files in a local folder (e.g., `~/kb-setup/seed/`):

- `interests.md` — your 1-page interest profile
- `topic_taxonomy.md` — distilled topics with evolution
- `url_sources.json` — extracted URL corpus (run `url_sources.py` on your Claude export)
- `subscriptions.md` — your active newsletter list
- `claude_messages_clean.md` — your Claude conversation export

The `url_sources.py` script and a recommended seed file structure live in this repo for reference.

### Step 3: Write the agent system prompts

Two markdown files defining each agent's behavior:

- `kb-blog-curator.system.md` — the blog agent's job description, KB schema, analysis template, calibration rules, file discipline, etc. (~25KB in the original setup)
- `tweet-kb-agent.system.md` — the tweet agent's equivalent (~10KB)

Both prompts are reproduced in this repo. **Adapt them to your KB's specifics** — the publication list, the topic taxonomy, the persona section. The structural sections (commit discipline, KB navigation, analysis template) should stay roughly the same.

### Step 4: Run setup

```bash
git clone <this repo>
cd Blog-ingestion-agent
pip install 'anthropic>=0.94.0' python-dotenv PyNaCl httpx

export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_PAT=github_pat_...   # the fine-grained PAT from prerequisites

python3 setup.py
```

This:
1. Uploads your 5-7 seed files via the Files API and persists the `file_id`s
2. Creates a Managed Agents environment (cloud sandbox config)
3. Creates the agent with your system prompt
4. Writes all IDs to `.env`

Repeat for the tweet agent (or use a single `setup.py` that handles both).

### Step 5: Deploy the GitHub Actions workflow

Two workflows go into the KB repo's `.github/workflows/`:

- `blog-ingest.yml` — cron 3x daily + manual trigger
- `cron-watchdog.yml` — hourly check for missed runs, auto-dispatch replacements

Plus the runtime script `scripts/run-blog-ingest.py` that the workflow invokes.

```bash
# Push files via curl (or just git commit + push from local clone of KB repo)
cp blog-ingest.yml ~/my-knowledge-base/.github/workflows/
cp cron-watchdog.yml ~/my-knowledge-base/.github/workflows/
cp run.py ~/my-knowledge-base/scripts/run-blog-ingest.py
cd ~/my-knowledge-base && git add -A && git commit -m "deploy agent workflows" && git push
```

### Step 6: Add GitHub Actions secrets

Six secrets on the KB repo (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `AGENT_ID` | From `.env` after running `setup.py` |
| `AGENT_VERSION` | From `.env` |
| `ENV_ID` | From `.env` |
| `SEED_FILE_IDS` | From `.env` (comma-separated `name:file_id` pairs) |
| `KB_REPO_PAT` | Your fine-grained PAT |

A `setup-secrets.py` script in this repo automates this via the GitHub API if your PAT has `Secrets: Read & write`.

### Step 7: Enable GitHub Pages (optional)

To browse your KB as a website (not just markdown on GitHub), add Quartz:

```bash
cp -r quartz/ ~/my-knowledge-base/
cp deploy-quartz.yml ~/my-knowledge-base/.github/workflows/
cd ~/my-knowledge-base && git add -A && git commit -m "add quartz" && git push
```

Then enable Pages in repo Settings → Pages → Source: GitHub Actions. The site builds and deploys on every push.

### Step 8: Trigger the first run

Manual trigger from the GitHub Actions tab (Actions → kb-blog-curator → Run workflow → slot=manual). The first run is slow (30-50 min) because it builds the RSS feed cache from cold. Subsequent runs are 15-25 min with the cache warm.

You'll see commits appear incrementally on `main` as the agent works through analyses one at a time.

---

## Operating the system

### Daily workflow

1. **Morning** — open the latest date folder on GitHub (`2026/04/15/`). The auto-generated `README.md` is your landing page. Read the synthesis files first; click into individual analyses for depth.
2. **Rate what you read** — at the top of any analysis, change `user_score:` to a number 0-10. Commit (one click on GitHub mobile). The agent picks this up on its next run and adjusts its interest model.
3. **Steer the agent** — open `_system/profile/feedback.md` and write free-form feedback ("more on RSI", "less governance retreads", "track 'AI-native SaaS' as a new theme"). Commit. The agent drains the inbox on the next run, updates `deltas.md`, and proceeds with the new bias.

### Monitoring

- **GitHub Actions tab** — see when each run fired, succeeded, or failed
- **Run log** — `2026/MM/DD/run-log-blog-<slot>.md` for the exhaustive audit (every fetch, every query, every cache decision)
- **Watchdog logs** — the hourly watchdog logs all dispatch decisions to its own workflow runs

### Updating the agent

When you want to change agent behavior (tweak the prompt, add a tool, adjust the cap):

```bash
# Edit kb-blog-curator.system.md locally
python3 setup.py --update          # bumps agent version, returns new version number
python3 sync-secret.py AGENT_VERSION <new>  # syncs to GitHub Actions secret
```

The agent is **versioned** — each update creates a new immutable version. Sessions can pin to a specific version for reproducibility, or use the latest. Old sessions running on prior versions don't break when you update.

### Costs

Real-world usage from the original setup:
- **Per blog-curator run:** ~$5-10 in token usage. Heavy use of prompt caching (6.9M cache reads vs 400K cache writes per run) keeps costs low.
- **Per day:** ~$15-30 with 3 daily runs.
- **Per month:** ~$450-900.

The tweet agent costs less per run (smaller analyses, no discovery phase). If you bookmark heavily (~50 tweets/day), expect another $200-400/month.

**Total: ~$700-1,300/month** for a fully-curated personal KB with deep daily synthesis. Cheaper than a research analyst.

You can reduce costs by:
- Running 1-2x daily instead of 3x
- Lowering the analysis cap (default: max 15, score ≥7)
- Using `claude-sonnet-4-6` instead of `claude-opus-4-6` (3-5x cheaper, slightly less depth)

### Failure modes and recovery

| Symptom | Cause | Fix |
|---|---|---|
| GitHub Actions cron skips a run | Known GH unreliability | The watchdog auto-dispatches within 1 hour |
| `git push` fails with 503 | Anthropic CMA git proxy issue | Agent system prompt embeds PAT in remote URL to bypass proxy |
| Session times out mid-analysis | Container 45-min limit | Incremental commit pattern means everything before the crash is durable |
| Agent confused by edge case | Bad prompt instruction | Edit `system.md`, `setup.py --update`, retry |
| Quartz deployment fails | Pages not configured | Settings → Pages → Source: GitHub Actions |
| Two agents conflict on git push | Concurrent push to main | Both agents do `pull --rebase` and retry up to 3x |

---

## What makes this work

A few principles the system depends on:

1. **Date-first organization.** you browses `2026/04/15/` and sees the day's content. Content-type-first organization (`analyses/`, `syntheses/`, `topics/` folders separately) buries today under months of history.
2. **Incremental commits.** Each analysis is one atomic commit+push. A session crash at analysis 5 of 12 leaves 1-4 safely in git. The next run dedupes via `blogs-ingested.jsonl` and only redoes 5-12.
3. **Versioned agents.** Agent prompts are persisted on Anthropic's side and versioned. Updates don't break in-flight sessions; rollback is one API call.
4. **Two-source interest model.** Static seed (ground truth, mounted every run) + evolving deltas (driven by feedback). The seed never moves; deltas grow over time. you can audit every shift in `evolution.md`.
5. **Persistent feed cache.** The agent doesn't re-probe RSS endpoints every run — it caches working feed URLs in `_system/profile/feed_map.json`. ~90% reduction in HTTP probes after the first run.
6. **Exhaustive audit logs.** Every fetch, every query, every cache decision is one chronological line in the run log. You can answer "did the agent check $publication this morning?" with a `grep`.
7. **Watchdog catches missed crons.** GH Actions cron is unreliable; a separate hourly watchdog detects and replays missed slots.
8. **Topics as cross-cutting indices.** Topic files aren't just lists of paths — they're navigable mini-indexes with summaries, key analyses tables, open questions. Both agents contribute cross-references. The KB's knowledge graph emerges from this.

---

## Repo contents

This `Blog-ingestion-agent` repo (separate from the KB itself) contains everything you need to set up your own version:

| File | Purpose |
|---|---|
| `kb-blog-curator.system.md` | Blog agent system prompt — adapt the persona/topics, keep the structure |
| `setup.py` | Creates/updates Anthropic environment + agent, uploads seed files |
| `run.py` | Runtime script invoked by GitHub Actions for each session |
| `migrate_repo.py` | One-shot migration script (date-first restructure) — useful if you start with the old layout |
| `blog-ingest.yml` | GitHub Actions workflow — cron 3x daily + manual trigger |
| `cron-watchdog.yml` | Hourly watchdog for missed cron runs |
| `quartz/` | Quartz config for browsable web view |
| `deploy-quartz.yml` | GitHub Actions workflow for Pages deployment |
| `subscriptions.md`, `topic_taxonomy.md`, `interests_seed.md` | Example seed files (replace with your own) |
| `url_sources.py` | Extract URL corpus from a Claude export |

---

## Why I built it this way

A few honest reflections from running this for myself:

- **Newsletter overload was the original problem.** I subscribed to ~85 substacks and was reading maybe 5% of what arrived. The agent doesn't just summarize — it picks the most thesis-relevant 5-15 per cycle and writes the kind of structured analysis I'd produce myself if I had unlimited time.
- **The synthesis is what I actually consume on my phone.** Individual analyses are reference material. The per-run synthesis at `2026/04/15/blog-synthesis-morning.md` is what I read while drinking coffee. Format matters: scannable headers, blockquoted talking points, surprising cross-references called out.
- **Topics files are the real KB asset.** Each topic file is a living mini-index of every analysis (from both agents) on that theme, with a summary that updates as understanding evolves. After 6 months, the topics files are how I navigate the KB — not date folders.
- **Feedback closes the loop.** Without `user_score:`, the agent has no signal on what's actually valuable to me. With ratings flowing in daily, the agent gradually shifts its monitoring priorities, ranking criteria, and topic emphasis.
- **Two agents > one agent.** Tweets and blogs are different content types. A tweet agent that processes batches of bookmarks works on a different cadence than a blog agent that hunts for new long-form content. Sharing the KB but keeping the agents separate keeps each one focused.

---

## Questions / contributions

If you set up your own version and hit issues, open an issue on this repo. If you have ideas for new features (sentiment scoring, multi-user support, API access to the KB), happy to discuss.

This isn't a polished open-source project — it's a working personal system with all the rough edges that implies. But the architecture has been validated end-to-end and is in daily production use. The README aims to give you enough to replicate it without needing to debug from scratch.
