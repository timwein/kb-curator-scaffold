# Personal Knowledge Base — Self-Curating with Claude Managed Agents

A knowledge base that **curates itself**. Four AI agents run on a schedule: three curate content into the KB (blogs, tweets, podcast interviews), and a fourth proactively bookmarks tweets from your X feed that match your established taste. Together they form a closed loop — your interests are discovered, filtered, analyzed, and fed back into the taste model end-to-end. You give 0-10 ratings to what you read; the agents learn and improve over time.

---

## What you get

Four agents running on staggered schedules, all writing into (or reading from) the same KB:

- **`tweet-bookmarker`** — scans your X Following and For You feeds **hourly from a local script** (6am–midnight), judges each candidate against your evolving taste profile via a Claude Managed Agents session, and bookmarks the ones that clearly match. Precision-over-recall: 0–10 bookmarks per run, with a confidence floor of 0.7. Runs locally because X auth needs your real browser session.
- **`tweet-kb-agent`** — ingests your bookmarked tweets (including those placed by the bookmarker above), writes a structured analysis per tweet, cross-references existing knowledge, and produces a per-run synthesis you can read on your phone.
- **`kb-blog-curator`** — monitors ~150 blogs/Substacks you subscribe to, hunts for new sources via web search, ranks candidates by relevance to your interests, picks the strongest, writes deep analyses, and commits them to the same KB.
- **`kb-podcast-curator`** — once daily, discovers podcast interview episodes relevant to your interests (via host/guest inversion from your url_sources, topic-driven web_search, and a pinned-shows list), retrieves transcripts (official → YouTube auto-captions → show-notes fallback), ranks them, and writes deep analyses of the strongest 0–3. Mirrors the blog agent's pipeline but targets long-form conversations instead of written content.

All three agents share the same GitHub repo (`<your-kb-repo>` in the original setup). The repo is organized **date-first** — you navigate `2026/04/15/` and see everything from that day side-by-side: blog analyses, tweet analyses, syntheses, and a daily README landing page.

The KB grows by accretion. Topics files cross-link tweet content with blog content. Over time, the agents discover new sources beyond your seed list. The bookmarker's taste profile is regenerated from the analyses the ingestion agent writes, so every rating you give feeds forward into *what* the bookmarker picks next. You give feedback by rating analyses (`user_score: 0-10`) — the agents adjust their interest model accordingly.

**Optional reader frontend.** GitHub is a poor reading surface for long-form analyses. You can deploy a Next.js reader to Vercel that gives you a private mobile-friendly reading surface, UI-based rating (commits `user_score` back to the KB so agents pick it up), and per-page chat with Claude grounded in the page content. The full reader lives in [`reader/`](./reader) in this repo — copy that directory into your own KB repo and deploy. See Step 7.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Triggers                                                       │
│                                                                  │
│  GitHub Actions (cloud cron, 3x daily blog + 1x daily podcast   │
│                 + watchdog hourly)                              │
│    • blog-ingest.yml        → kicks off blog curator            │
│    • podcast-ingest.yml     → kicks off podcast curator         │
│    • cron-watchdog.yml      → catches missed runs (both)        │
│    • (bookmark workflow)    → kicks off tweet ingestion agent   │
│                                                                  │
│  launchd / cron (local, hourly 6am-midnight — runs on laptop)   │
│    • com.<you>.tweet-bookmarker → kicks off bookmarker          │
└───┬────────────────────────────┬────────────────────────────────┘
    │  POST /v1/sessions         │  Local Python + Playwright
    ▼                            ▼
┌───────────────────────────────────┐  ┌──────────────────────┐
│  Anthropic Managed Agents (cloud) │  │  Your laptop         │
│  ┌─────────────────┐ ┌──────────┐ │  │  ┌────────────────┐  │
│  │ tweet-kb-agent  │ │kb-blog-  │ │  │  │tweet-bookmarker│  │
│  │ (ingestion)     │ │curator   │ │  │  │(orchestrator)  │  │
│  └────────┬────────┘ └────┬─────┘ │  │  │• scrapes feeds │  │
│           │ git push       │      │  │  │  via Playwright│  │
└───────────┼────────────────┼──────┘  │  │• CMA session   │  │
            │                │         │  │  for judgment ◄┼──┼──POST /v1/sessions
            │                │         │  │• Playwright    │  │
            │                │         │  │  clicks bookmark│ │
            │                │         │  └────┬───────────┘  │
            │                │         │       │ git push     │
            │                │         │       │ (considered  │
            │                │         │       │  log only)   │
            │                │         │       │              │
            │                │         │       │        ┌─────┼──┐
            │                │         │       │        │ x.com│  │
            │                │         │       │        │ book-│  │
            │                │         │       │        │ marks│  │
            │                │         │       │        └──▲───┘  │
            │                │         │       │           │      │
            │                │         │       │           │ click│
            │                │         │       │           └──────┤
            │                │         │       │ (next tweet-kb   │
            │                │         │       │  run picks these │
            │                │         │       │  up via X API)   │
            │                │         └───────┼──────────────────┘
            │                │                 │
            ▼                ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Repo: <your>-knowledge-base (private)                   │
│                                                                  │
│  2026/                                                           │
│    └── 04/                                                       │
│        └── 15/                                                   │
│            ├── README.md                    ← daily landing page │
│            ├── blog-<pub>-<slug>.md         ← blog analyses      │
│            ├── blog-synthesis-morning.md    ← blog digest        │
│            ├── podcast-<show>-<slug>.md     ← podcast analyses   │
│            ├── podcast-synthesis-daily.md   ← podcast digest     │
│            ├── <tweet-id>-<author>-<slug>.md ← tweet analyses    │
│            ├── tweet-synthesis-morning.md   ← tweet digest       │
│            └── run-log-blog-morning.md      ← exhaustive audit   │
│  topics/                                                         │
│    └── <topic-slug>.md                      ← cross-cutting refs │
│  _system/                                                        │
│    ├── profile/                             ← blog agent's       │
│    │   ├── deltas.md                          interest model     │
│    │   ├── feedback.md                                            │
│    │   ├── feed_map.json                    ← RSS cache          │
│    │   ├── discovered_sources.md                                  │
│    │   ├── bookmark-taste-profile.md        ← bookmarker's       │
│    │   │                                      taste doc (auto-   │
│    │   │                                      regenerated)       │
│    │   └── bookmark-considered.jsonl        ← bookmarker dedup   │
│    ├── profile-podcast/                     ← podcast agent's    │
│    │   ├── deltas.md                          interest model     │
│    │   ├── pinned_shows.md                  ← must-check shows   │
│    │   ├── show_feed_map.json               ← transcript cache   │
│    │   └── discovered_shows.md                                    │
│    ├── meta/                                                      │
│    │   ├── ingested.jsonl                   ← tweet dedupe       │
│    │   ├── blogs-ingested.jsonl             ← blog dedupe        │
│    │   └── podcasts-ingested.jsonl          ← podcast dedupe     │
│    └── seed/                                ← your interest seed │
└─────────────────────────────────────────────────────────────────┘
```

The two content-curating agents (`tweet-kb-agent`, `kb-blog-curator`) run fully on Anthropic's cloud, triggered by GitHub Actions cron — **nothing runs on your laptop for them**. The `tweet-bookmarker` is the exception: it runs locally via launchd (or cron on Linux) because X's authenticated write APIs need cookies from your real Chrome session that can't be shared with a remote container. The bookmarker still uses a CMA session for the judgment step — only the feed scraping and bookmark clicks happen locally.

---

## The agents in detail

### `tweet-bookmarker`

**Trigger:** launchd (macOS) or cron (Linux), hourly 06:00–00:00 local time. 19 fire times per day; quiet window overnight.

**What it does:** scrapes your X Following (chronological) and For You (algorithmic) feeds via Playwright using cookies from your real Chrome session, filters to thread roots (no retweets, no replies, no promoted), dedupes against `meta/ingested.jsonl` and `_system/profile/bookmark-considered.jsonl`, then sends the candidate list to a Claude Managed Agents session. The session reads `_system/profile/bookmark-taste-profile.md` plus a few recent analyses for grounding, judges each candidate, and calls a `bookmark_tweet(tweet_id, author, reason, confidence)` custom tool for the ones that match. The local orchestrator receives those tool calls and performs the actual X bookmark click with Playwright — the CMA container never sees your Chrome cookies. This is the "credentials host-side via custom tools" pattern from Anthropic's Managed Agents client-patterns guide.

**Budget and calibration:** up to 10 bookmarks per run (hard cap, orchestrator-enforced). Confidence floor of 0.7 — calls below that are silently skipped and the agent is told why, as a calibration signal.

**Outputs to the KB:**
- `_system/profile/bookmark-considered.jsonl` — append-only log of every candidate the bookmarker has ever evaluated (bookmarked or not), so next-hour runs never re-consider the same tweet. Committed on every run that surfaces new candidates.
- The bookmarks it places on X then flow into `tweet-kb-agent`'s ingestion queue on its next scheduled run, so the closed loop (bookmark → analyze → taste-profile refresh → next bookmark round) happens without manual intervention.

**Seed for the taste profile:** generated one-time (and re-runnable monthly) by a `build_taste_profile.py` script from the corpus of past analyses in `YYYY/MM/DD/` plus topic signals from `_system/seed/`. The profile ranks favored authors by `count × avg_relevance`, lists favored topics, and surfaces calibration exemplars (high and low relevance) for the agent to anchor on.

**Where the code lives:** [`tweet-agents/`](./tweet-agents) in this repo. That directory ships the orchestrator (`run_bookmarker.py`), feed fetcher (`lib/feed_fetcher.py`), Playwright bookmark-click action (`lib/bookmarker.py`), agent system prompt (`lib/bookmark_prompts.py`), taste-profile generator (`build_taste_profile.py`), and a launchd plist template. Keep your `config.json` and any filled-in plist out of version control — both hold credentials (the scaffold's `.gitignore` already excludes them). Only the runtime artifacts (taste profile + considered log) live in the KB itself.

### `tweet-kb-agent`

**Trigger:** Whenever you push a batch of bookmarked tweets to a queue file (e.g., via a Shortcut or browser extension that exports your X bookmarks). The agent processes the new tweets in the next session. The `tweet-bookmarker` above also pushes bookmarks onto this queue automatically — the two agents chain.

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

### `kb-podcast-curator`

**Trigger:** Cron, once daily at 12:30 PT. Plus manual trigger and the same hourly watchdog that catches missed blog runs.

**Per-run pipeline:**

1. **Load profile** — same seed files as the blog agent (shared `SEED_FILE_IDS` — the podcast agent reuses the blog agent's Files API uploads), plus the podcast agent's own evolving deltas at `_system/profile-podcast/deltas.md`.
2. **Drain feedback inbox** — `_system/profile-podcast/feedback.md` (separate from the blog agent's).
3. **Passive learning** — scan recent commits for `user_score:` ratings Tim added to podcast analyses since the last run; update deltas based on calibration gaps.
4. **Discovery (two co-equal legs):**
   - **Tier 0 pinned shows** (`_system/profile-podcast/pinned_shows.md`) — a starter list of ~10 transcript-friendly AI/VC podcasts (Dwarkesh, Lex Fridman, Latent Space, Lenny's, Cognitive Revolution, 20VC, a16z, Sequoia Training Data, AI & I, TED AI Show) is seeded on first run. Tim edits over time.
   - **New show hunt** — 8–12 `web_search` queries across topic-driven queries, **host/guest inversion** (for the top 10 authors by `total_count` in `url_sources.json`, search `"<name>" podcast interview 2026 transcript` — authors Tim has referenced repeatedly in writing often give podcast interviews on the same theses), site-scoped queries against pinned shows, and "what's new" sweeps.
5. **Transcript retrieval (per candidate)** — fallback chain: **official transcript → YouTube auto-captions → substantial show notes → skip**. Every attempt is logged. YouTube captions get a `-1` relevance downgrade; show notes get `-2`.
6. **Rank + cap** — score each candidate 1–10 with extra weight for guest-signal (guest appears in `url_sources.json`) and transcript quality. Keep everything scored 8 or above, **max 3 per run** (stricter than blog because transcripts are 15–40k words and each analysis is meaningfully more expensive).
7. **Analyze each winner** — write a structured analysis (same template as blog + a `## Direct Quotes` section capturing verbatim excerpts from the conversation, since podcasts preserve conversational texture), update topic cross-references, commit and push **incrementally**.
8. **Synthesize + update daily README** — write `podcast-synthesis-daily.md` and add a Podcast Curator section to the shared `YYYY/MM/DD/README.md` (reads the file first, updates only its own section).

**Zero-analyses runs are legitimate.** If nothing from today's discovery clears the score-8 bar, the synthesis says "no episodes met the quality bar today" and the run ends. No backfilling with mediocre picks.

**Audit log:** Same exhaustive-audit pattern as the blog agent — `YYYY/MM/DD/run-log-podcast-daily.md` logs every show probed, every `web_search` issued, every transcript attempt with its HTTP status and character count, every candidate considered and why it was selected or skipped.

**Dedupe:** Per-episode dedupe via `_system/meta/podcasts-ingested.jsonl` — separate from the blog agent's `blogs-ingested.jsonl` so the two agents never collide.

**Single-URL mode (optional):** If you deploy the reader frontend (Step 8) and it dispatches `podcast-ingest.yml` with a `url` input, the agent bypasses discovery and analyzes just that one episode. Same pattern as the blog agent's reader-app hook; enable by adding a `url` workflow_dispatch input and reading `SINGLE_URL` in `podcast-run.py` (scaffold version ships without this to match the blog agent's minimal pattern).

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

Three markdown files defining each content-curating agent's behavior:

- `agents/kb-blog-curator.system.md` — the blog agent's job description, KB schema, analysis template, calibration rules, file discipline, etc. (~50KB in the scaffold version)
- `agents/kb-podcast-curator.system.md` — the podcast agent's equivalent, with a transcript-discovery pipeline and a `## Direct Quotes` section in the analysis template (~50KB)
- The tweet-kb-agent's system prompt lives under `tweet-agents/lib/prompts.py`

All prompts are reproduced in this repo. **Adapt them to your KB's specifics** — the publication list, the topic taxonomy, the persona section, the pinned-shows starter list in the podcast prompt. The structural sections (commit discipline, KB navigation, analysis template, incremental-commit discipline, stop semantics) should stay roughly the same.

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
2. Creates a Managed Agents environment (cloud sandbox config) for the blog agent
3. Creates the blog agent with your system prompt
4. Writes all IDs to `.env`

Then stand up the podcast agent — it **reuses** the blog agent's seed files, so this is a fast second step:

```bash
python3 scripts/podcast-setup.py
```

This reads `SEED_FILE_IDS` from `.env` (set by `setup.py` above), creates a separate environment + agent for the podcast curator, and writes `PODCAST_ENV_ID`, `PODCAST_AGENT_ID`, `PODCAST_AGENT_VERSION` to `.env` — never clobbering the blog agent's keys.

Repeat the same pattern for the tweet agent (its setup lives under `tweet-agents/` — see Step 7).

### Step 5: Deploy the GitHub Actions workflows

Three workflows go into the KB repo's `.github/workflows/`:

- `blog-ingest.yml` — blog curator, cron 3x daily + manual trigger
- `podcast-ingest.yml` — podcast curator, cron 1x daily @ 12:30 PT + manual trigger
- `cron-watchdog.yml` — hourly check for missed runs on both workflows, auto-dispatch replacements

Plus two runtime scripts that the workflows invoke:

- `scripts/run-blog-ingest.py`
- `scripts/run-podcast-ingest.py`

```bash
# Push files via curl (or just git commit + push from local clone of KB repo)
cp .github/workflows/blog-ingest.yml    ~/my-knowledge-base/.github/workflows/
cp .github/workflows/podcast-ingest.yml ~/my-knowledge-base/.github/workflows/
cp .github/workflows/cron-watchdog.yml  ~/my-knowledge-base/.github/workflows/
cp scripts/run.py         ~/my-knowledge-base/scripts/run-blog-ingest.py
cp scripts/podcast-run.py ~/my-knowledge-base/scripts/run-podcast-ingest.py
cd ~/my-knowledge-base && git add -A && git commit -m "deploy agent workflows" && git push
```

### Step 6: Add GitHub Actions secrets

Nine secrets on the KB repo (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `AGENT_ID` | Blog agent — from `.env` after running `setup.py` |
| `AGENT_VERSION` | Blog agent — from `.env` |
| `ENV_ID` | Blog agent — from `.env` |
| `PODCAST_AGENT_ID` | Podcast agent — from `.env` after running `podcast-setup.py` |
| `PODCAST_AGENT_VERSION` | Podcast agent — from `.env` |
| `PODCAST_ENV_ID` | Podcast agent — from `.env` |
| `SEED_FILE_IDS` | From `.env` (shared by both agents — comma-separated `name:file_id` pairs) |
| `KB_REPO_PAT` | Your fine-grained PAT |

A `setup-secrets.py` script in this repo automates this via the GitHub API if your PAT has `Secrets: Read & write`. Alternatively, `gh secret set <NAME> --repo <owner>/<kb-repo> --body <value>` works per secret.

### Step 7: Deploy the tweet agents (ingestion + bookmarker)

The tweet-kb-agent (analysis + commit) and tweet-bookmarker (hourly feed scoring) both live under [`tweet-agents/`](./tweet-agents) and share a single Python package. Both run locally on your Mac — tweet ingestion because X's authenticated feeds are easier from a real Chrome profile, bookmarking because the write action (clicking Bookmark) must happen from your authenticated session.

Detailed walkthrough: [`tweet-agents/SETUP.md`](./tweet-agents/SETUP.md). The short version:

```bash
cd tweet-agents
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp config.example.json config.json
# edit config.json → set github_repo_url to your own KB repo

export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_PAT=github_pat_...

python setup_tweet_ingest.py   # creates the tweet-kb-agent
python setup_bookmarker.py     # creates the tweet-bookmark-agent

python run_tweet_ingest.py     # manual first run (analyzes your current bookmarks)
python run_bookmarker.py       # manual first run (scores feeds + bookmarks top picks)
```

Once manual runs are clean:

- Schedule `run_tweet_ingest.py` 3×/day (`python make_launchd.py` generates a LaunchAgent plist).
- Schedule `run_bookmarker.py` hourly 06:00–00:00 using `com.example.tweet-bookmarker.plist.template` as a starting point. Fill in the paths + secrets and drop it into `~/Library/LaunchAgents/`. **Never commit a filled-in plist** — the `.gitignore` excludes it.

### Step 8: Deploy the reader (optional Vercel frontend)

The KB is plain markdown on GitHub, which is great for agents and source-of-truth storage but rough for reading on mobile. The [`reader/`](./reader) directory is a Next.js app ready to deploy to Vercel.

What the reader gives you:

- Mobile + desktop reading with real typography, navigation, and search across every analysis
- 0-10 rating from the UI; the reader patches `user_score:` in the page frontmatter and commits to `main`, so the agents pick up your feedback on their next run
- Per-page chat with Claude — the full page body is prepended as system prompt so answers are grounded in the analysis. Ephemeral; nothing persisted

**To deploy**:

1. Copy the `reader/` directory from this scaffold into your KB repo root. Commit + push.
2. Create a Vercel project from your KB repo. Set **Root Directory** = `reader`.
3. Add env vars (template at `reader/.env.local.example`): `GITHUB_REPO`, `GITHUB_BRANCH`, `GITHUB_TOKEN` (fine-grained PAT with `contents: write` scoped only to this repo), `ANTHROPIC_API_KEY`, `GITHUB_WEBHOOK_SECRET` (random string), and either the two `CF_ACCESS_*` vars (if you're gating via Cloudflare Access) or `SKIP_ACCESS_VERIFY=true` (to defer auth). Alternative: set `BASIC_AUTH_USER` + `BASIC_AUTH_PASSWORD` for a simpler HTTP Basic Auth gate.
4. Add a GitHub webhook on your KB repo: Settings → Webhooks → Add. URL = `https://<your-vercel-host>/api/revalidate`, same secret, "Just the push event". This triggers on-demand ISR when agents commit.
5. **(Optional)** Gate behind Cloudflare Access for private access. Requires a custom domain you own (Access can't protect `*.vercel.app` hostnames). Instructions in [`reader/README.md`](./reader/README.md).

**Gotcha**: Vercel Hobby refuses to deploy commits whose committer can't be associated with a GitHub user. If your agents commit as `<name>@local` or any synthetic email, change their `git config user.email` to a GitHub-verified email on your account (`GIT_COMMITTER_EMAIL` env var if using `run_bookmarker.py` / `build_taste_profile.py`). Also avoid `Co-Authored-By` trailers on your own commits — Hobby treats them as multi-author collaboration and blocks.

Reader design details: [`reader/SPEC.md`](./reader/SPEC.md).

### Step 9: Trigger the first runs

**Blog curator:** Manual trigger from the GitHub Actions tab (Actions → kb-blog-curator → Run workflow → slot=manual). The first run is slow (30-50 min) because it builds the RSS feed cache from cold. Subsequent runs are 15-25 min with the cache warm.

**Podcast curator:** Separately dispatch Actions → kb-podcast-curator → Run workflow → slot=manual. The first run takes 20-40 min because it bootstraps `_system/profile-podcast/` (deltas, pinned_shows starter list, empty show_feed_map, etc.) and probes transcript sources from scratch. A first-run outcome of **zero analyses** is legitimate — if nothing clears the score-8 bar on day one, the agent writes an empty synthesis and stops.

You'll see commits appear incrementally on `main` as each agent works through analyses one at a time. Both agents push to the same repo — rebase+retry handles the occasional concurrent push.

---

## Operating the system

### Daily workflow

1. **Morning** — open the reader (if deployed per Step 7), or the latest date folder on GitHub. The daily `README.md` is your landing page; read the synthesis files first, click into individual analyses for depth.
2. **Rate what you read** — click a 0-10 score on any analysis page in the reader, or edit `user_score:` at the top of the page on GitHub. Both paths commit back to the repo; the agent picks this up on its next run and adjusts its interest model.
3. **Steer the agents** — each agent has its own feedback inbox:
   - Blog: `_system/profile/feedback.md`
   - Podcast: `_system/profile-podcast/feedback.md`

   Write free-form feedback ("more on RSI", "less governance retreads", "track 'AI-native SaaS' as a new theme", "prioritize podcasts with guests from the interconnects.ai author list"). Commit. Each agent drains its own inbox on the next run, updates its respective `deltas.md`, and proceeds with the new bias.

### Monitoring

- **GitHub Actions tab** — see when each run fired, succeeded, or failed
- **Blog run log** — `2026/MM/DD/run-log-blog-<slot>.md` for the blog agent's exhaustive audit (every fetch, every query, every cache decision)
- **Podcast run log** — `2026/MM/DD/run-log-podcast-daily.md` for the podcast agent's equivalent (every show probed, every transcript attempt with HTTP status, every candidate considered)
- **Watchdog logs** — the hourly watchdog logs all dispatch decisions to its own workflow runs, covering both blog and podcast slots
- **Bookmarker log (local)** — `tail -f /tmp/tweet-bookmarker.log` on your laptop streams every hourly bookmarker run; check `_system/profile/bookmark-considered.jsonl` on GitHub for the per-run audit of what was evaluated and the agent's reasoning per decision

### Updating the agent

When you want to change agent behavior (tweak the prompt, add a tool, adjust the cap):

```bash
# Blog agent
python3 scripts/setup.py --update                    # bumps AGENT_VERSION
python3 sync-secret.py AGENT_VERSION <new>           # syncs to GitHub Actions secret

# Podcast agent
python3 scripts/podcast-setup.py --update            # bumps PODCAST_AGENT_VERSION
python3 sync-secret.py PODCAST_AGENT_VERSION <new>   # syncs to GitHub Actions secret
```

Each agent is **independently versioned** — each update creates a new immutable version. Sessions can pin to a specific version for reproducibility, or use the latest. Old sessions running on prior versions don't break when you update, and updating one agent never affects the other.

### Costs

Real-world usage from the original setup:
- **Per blog-curator run:** ~$5-10 in token usage. Heavy use of prompt caching (6.9M cache reads vs 400K cache writes per run) keeps costs low.
- **Per podcast-curator run:** ~$10-20. Lower cap (max 3 vs blog's max 15) but much longer inputs — transcripts are typically 15k-40k words each, vs 2k-5k for a blog post.
- **Per day:** ~$25-50 across all scheduled content agents (3 blog runs + 1 podcast run).
- **Per month:** ~$750-1,500.

The tweet agent costs less per run (smaller analyses, no discovery phase). If you bookmark heavily (~50 tweets/day), expect another $200-400/month.

**Total: ~$950-1,900/month** for a fully-curated personal KB with deep daily synthesis across blogs, tweets, and podcasts. Cheaper than a research analyst.

You can reduce costs by:
- Running the blog curator 1-2x daily instead of 3x
- Lowering the blog analysis cap (default: max 15, score ≥7) or the podcast cap (default: max 3, score ≥8)
- Using `claude-sonnet-4-6` instead of `claude-opus-4-6` (3-5x cheaper, slightly less depth)

### Failure modes and recovery

| Symptom | Cause | Fix |
|---|---|---|
| GitHub Actions cron skips a run | Known GH unreliability | The watchdog auto-dispatches within 1 hour |
| `git push` fails with 503 | Anthropic CMA git proxy issue | Agent system prompt embeds PAT in remote URL to bypass proxy |
| Session times out mid-analysis | Container 45-min limit | Incremental commit pattern means everything before the crash is durable |
| Agent confused by edge case | Bad prompt instruction | Edit `system.md`, `setup.py --update`, retry |
| Reader deploy fails on Vercel | Committer email isn't GitHub-verified | Set `GIT_COMMITTER_EMAIL` env var (see Step 8 Gotcha) |
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
8. **Topics as cross-cutting indices.** Topic files aren't just lists of paths — they're navigable mini-indexes with summaries, key analyses tables, open questions. All four agents contribute cross-references — a topic file like `agent-reliability.md` ends up with blog analyses, tweet analyses, and podcast analyses in the same Key Analyses table, sorted by date. The KB's knowledge graph emerges from this.
9. **Hybrid local/cloud where auth forces it.** Ingestion and blog curation run fully cloud-side because their tools (git, web_fetch, web_search) don't need your personal auth. Bookmarking writes back to X, which requires your authenticated session — so the bookmarker keeps the judgment in a cloud CMA session but does the actual bookmark click locally via Playwright. Your Chrome cookies never leave your laptop. This is the "credentials host-side via custom tools" pattern from Anthropic's Managed Agents client-patterns guide — applicable any time an agent needs to act on a service whose auth is bound to a real user session (Slack, Gmail, banking, etc.).
10. **The bookmarking agent closes the loop.** Tweets you'd never have noticed on X (scrolling burns time; bookmarks are already chosen) now surface automatically. The taste profile the bookmarker reads is derived from the analyses the ingestion agent writes, which are rated by you — so the system gradually gets better at bookmarking without any retraining step.

---

## Repo contents

This scaffold is everything you need to stand up the full system — four managed agents, the KB repo conventions, and the reader frontend. Clone it, adapt the persona/topics + repo URLs to your own, and follow the steps above. The scaffold itself is **separate** from the KB repo it configures: you keep this repo for setup code, and the agents populate a private KB repo elsewhere.

| Path | Purpose |
|---|---|
| `agents/kb-blog-curator.system.md` | Blog agent system prompt — adapt the persona/topics, keep the structure |
| `agents/kb-podcast-curator.system.md` | Podcast agent system prompt — transcript-discovery pipeline, `## Direct Quotes` section, pinned-shows starter list |
| `scripts/setup.py` | Blog agent: creates/updates Anthropic environment + agent, uploads seed files |
| `scripts/run.py` | Blog agent: runtime script invoked by GitHub Actions for each session |
| `scripts/podcast-setup.py` | Podcast agent: reuses blog `SEED_FILE_IDS`, creates a separate env + agent for the podcast curator |
| `scripts/podcast-run.py` | Podcast agent: runtime script invoked by `podcast-ingest.yml` |
| `scripts/migrate_repo.py` | One-shot migration script (date-first restructure) — useful if you start with an older content-type-first layout |
| `scripts/url_sources.py` | Extract URL corpus from a Claude.ai conversation export |
| `.github/workflows/blog-ingest.yml` | Blog agent workflow — cron 3x daily + manual trigger |
| `.github/workflows/podcast-ingest.yml` | Podcast agent workflow — cron 1x daily @ 12:30 PT + manual trigger |
| `.github/workflows/cron-watchdog.yml` | Hourly watchdog for missed cron runs on both workflows |
| `seed-templates/` | Example seed files (interests, topic taxonomy, subscriptions) — replace with your own |
| `tweet-agents/` | Local orchestrator for the tweet-kb-agent + tweet-bookmarker (Python + Playwright). See `tweet-agents/SETUP.md`. |
| `tweet-agents/lib/prompts.py` | Tweet ingestion agent system prompt |
| `tweet-agents/lib/bookmark_prompts.py` | Bookmarker agent system prompt + custom-tool schema |
| `tweet-agents/build_taste_profile.py` | Regenerates the bookmarker's taste profile from the corpus of rated analyses |
| `tweet-agents/com.example.tweet-bookmarker.plist.template` | launchd template — fill in paths + secrets, never commit the filled version |
| `reader/` | Next.js app you deploy to Vercel as a mobile-friendly reading surface. See `reader/SPEC.md` + `reader/README.md`. |

---

## Why I built it this way

A few honest reflections from running this for myself:

- **Newsletter overload was the original problem.** I subscribed to ~85 substacks and was reading maybe 5% of what arrived. The agent doesn't just summarize — it picks the most thesis-relevant 5-15 per cycle and writes the kind of structured analysis I'd produce myself if I had unlimited time.
- **The synthesis is what I actually consume on my phone.** Individual analyses are reference material. The per-run synthesis at `2026/04/15/blog-synthesis-morning.md` is what I read while drinking coffee. Format matters: scannable headers, blockquoted talking points, surprising cross-references called out.
- **Topics files are the real KB asset.** Each topic file is a living mini-index of every analysis (from both agents) on that theme, with a summary that updates as understanding evolves. After 6 months, the topics files are how I navigate the KB — not date folders.
- **Feedback closes the loop.** Without `user_score:`, the agent has no signal on what's actually valuable to me. With ratings flowing in daily, the agent gradually shifts its monitoring priorities, ranking criteria, and topic emphasis.
- **Two agents > one agent.** Tweets and blogs are different content types. A tweet agent that processes batches of bookmarks works on a different cadence than a blog agent that hunts for new long-form content. Sharing the KB but keeping the agents separate keeps each one focused.
- **Three agents > two agents — once you have ingestion working, the bottleneck shifts upstream.** With the ingestion + blog agents producing high-quality analyses, the limiting factor became *which tweets get bookmarked in the first place*. I was missing high-signal posts because I scrolled X infrequently and inconsistently. The bookmarking agent solves this by watching the feeds for me, hourly, every hour I'm awake — applying the same taste model I'd apply if I were paying attention. The 0.7 confidence floor + 10/run cap keeps the bar high enough that the ingestion agent isn't fed noise.

---

## Questions / contributions

If you set up your own version and hit issues, open an issue on this repo. If you have ideas for new features (sentiment scoring, multi-user support, API access to the KB), happy to discuss.

This isn't a polished open-source project — it's a working personal system with all the rough edges that implies. But the architecture has been validated end-to-end and is in daily production use. The README aims to give you enough to replicate it without needing to debug from scratch.
