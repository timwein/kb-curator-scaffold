# Tweet KB Reader — Spec

## 1. Product Vision

A private, fast reading surface for the self-curating knowledge base at
`github.com/<your-username>/<your-kb-repo>`. Optimized for long-form analyses on
desktop and mobile. Two pieces of active functionality beyond reading:

1. **Relevance rating** — patch `user_score` (0-10) on analysis pages. Writes
   back to the KB repo so the managed agents (`tweet-kb-agent`,
   `kb-blog-curator`) see the feedback on their next run.
2. **Per-page chat with Claude** — ask follow-up questions with the full page
   body automatically prepended as context. Ephemeral; no server-side history.

GitHub stays the source of truth. The reader is a projection; the agents are
unchanged.

## 2. Tech Stack

| Layer          | Choice                                    | Why                                                                                          |
| -------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------- |
| Framework      | Next.js 15 (App Router, TypeScript)       | SSG + API routes in one deploy                                                               |
| Styling        | Tailwind CSS + `@tailwindcss/typography`  | Reading UI is 90% prose; `prose` class does most of the heavy lifting                        |
| Markdown       | `next-mdx-remote` + `remark-gfm`          | Server-rendered MDX, GFM tables/footnotes, no client MD parsing                              |
| Frontmatter    | `gray-matter` (+ custom `<details>` pre-parse) | KB frontmatter is embedded in a `<details>` block, not top-of-file YAML — see §5         |
| Hosting        | Vercel (free/Hobby tier)                  | Auto-deploy on push, ISR, zero-config Next.js                                                |
| Auth           | Cloudflare Access (in front of Vercel)    | One-time email PIN to the owner's email; no password, works across devices                   |
| Chat           | `@anthropic-ai/sdk`, server-side only     | API key stays on Vercel; client never sees it                                                |
| Rating writeback | GitHub REST API (contents endpoint) via `octokit` | Fine-grained PAT, patches single file, auto-commits `chore(rating): ...`               |

Deploy target: Vercel, root `reader/`, one environment (production). No
staging.

## 3. Repo Layout

Reader lives inside the KB repo:

```
tweet-knowledge-base/
├── 2024/ 2026/ …              ← KB content (unchanged, read at build)
├── topics/ syntheses/ _system/
├── quartz/                    ← DELETE (abandoned earlier approach)
└── reader/                    ← NEW: Next.js app
    ├── app/
    ├── lib/
    ├── components/
    ├── public/
    ├── package.json
    ├── tailwind.config.ts
    ├── next.config.ts
    └── .env.local.example
```

Vercel project config:
- **Root Directory**: `reader`
- **Build Command**: `next build`
- **Install Command**: `npm install`
- Environment variables: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`
  (= `<your-username>/<your-kb-repo>`), `GITHUB_BRANCH` (= `main`).

## 4. Information Architecture

### 4.1 URL structure

| Route                               | Purpose                                                                 |
| ----------------------------------- | ----------------------------------------------------------------------- |
| `/`                                 | Home. Latest day's landing page (rendered `README.md` for newest date). |
| `/days`                             | Reverse-chron list of all day folders (`2026/04/14`, …).                |
| `/day/[yyyy]/[mm]/[dd]`             | Daily landing page (`/day/2026/04/14` → `2026/04/14/README.md`).        |
| `/p/[yyyy]/[mm]/[dd]/[slug]`        | Individual page (blog analysis, tweet analysis, or synthesis).          |
| `/topics`                           | Index of `topics/*.md`.                                                 |
| `/topics/[slug]`                    | Topic page.                                                             |
| `/syntheses`                        | Index of `syntheses/**/*.md`.                                           |
| `/syntheses/[...path]`              | Synthesis page.                                                         |
| `/search`                           | Client-side fuzzy search over title + TL;DR + topics.                   |
| `/api/rate`                         | POST — write `user_score` to a page's frontmatter block (GitHub commit). |
| `/api/chat`                         | POST — proxy to Anthropic Messages API with page context prepended.     |
| `/api/revalidate`                   | POST (GitHub webhook) — on-demand ISR refresh after agent commits.      |

### 4.2 Page taxonomy (for interactive-footer eligibility)

Rule: a page qualifies for rating + chat iff **it has a `user_score` field in
its embedded frontmatter AND its filename does NOT start with `run-log-`**.

That's the cleanest test. It covers:
- ✅ `blog-*.md` — yes
- ✅ `<digits>-<author>-*.md` (tweet analyses) — yes
- ✅ `*-synthesis-*.md` — yes (syntheses are rateable per agent design)
- ❌ `run-log-*.md` — no (internal audit; hidden from nav entirely)
- ❌ `README.md` (daily landing) — no (aggregation page)
- ❌ `topics/*.md` — no (cross-ref surface)

`run-log-*.md` files are excluded from listings by the same rule applied at
nav generation time.

### 4.3 Layout

- **Desktop** (≥1024px): max-width 720px prose column, left sidebar with
  day-tree + topics + search, right rail with metadata (topics, relevance,
  source URL, rating widget sticky).
- **Mobile** (<1024px): single column. Top nav = hamburger → day-tree. Rating
  + chat live in a sticky bottom sheet that collapses.
- Typography: system-ui, 17px base / 1.65 line-height, `prose-stone` (light)
  / `prose-invert` (dark). Respects `prefers-color-scheme`.

## 5. Data Model — Parsing KB Pages

KB pages look like this:

```markdown
# [Title](https://…)
*By Author · Publication · Published YYYY-MM-DD*

<details><summary><strong>Metadata</strong> · <em>Pub</em> · relevance 7/10 · midday</summary>

```yaml
source_type: substack
url: "…"
topics: [...]
relevance_score: 7
user_score:
slot: midday
```

</details>

---

## TLDR
…
```

The frontmatter is **inside** a `<details>` block, wrapped in a ```yaml …
``` fence. Standard `gray-matter` won't find it.

### Parsing strategy

`lib/kb/parse.ts`:

1. Read raw file.
2. Regex-extract the first `<details>…</details>` block.
3. Within it, regex-extract the ```yaml fenced block.
4. `yaml.parse()` that block → `frontmatter` object.
5. Strip the `<details>…</details>` block from the body before passing to MDX
   renderer (we surface frontmatter in the right rail instead).
6. Extract the leading `# [Title](url)\n*By …*` header block into a
   structured `header` object (`{ title, url, byline }`).

The regex is forgiving: tolerates missing summary, extra whitespace, trailing
`---`. Tests in §10.

### Typed model (`lib/kb/types.ts`)

```ts
type PageKind = 'blog' | 'tweet' | 'synthesis' | 'daily' | 'topic' | 'runlog' | 'other';

interface KbPage {
  // identity
  path: string;                // e.g. "2026/04/14/blog-foo.md"
  slug: string;                // e.g. "blog-foo"
  kind: PageKind;
  date: string | null;         // ISO "2026-04-14" if in a YYYY/MM/DD folder
  // display
  title: string;
  url?: string;                // source URL if present
  byline?: string;             // "Alberto Romero · The Algorithmic Bridge · 2026-04-13"
  // frontmatter (raw + typed convenience)
  frontmatter: Record<string, unknown>;
  userScore: number | null;     // 0..10 or null if blank
  relevanceScore: number | null;
  topics: string[];
  sourceType?: string;
  slot?: string;
  // body
  body: string;                // markdown with the <details> block stripped
  // interactive eligibility
  canRate: boolean;            // has `user_score` field AND kind !== 'runlog'
  canChat: boolean;            // same condition
}
```

### Build-time scan

At build, `lib/kb/scan.ts` walks the parent directory (`../`) from `reader/`:

- Globs: `{2024,2025,2026}/**/*.md`, `topics/*.md`, `syntheses/**/*.md`.
- Excludes: `_system/`, `scripts/`, `meta/`, `reader/`, `quartz/`, `.github/`.
- For each file → `KbPage`.
- Outputs a `pages.json` manifest used by nav and search.

## 6. Feature Modules

### 6.1 Reader (read-only)

- MDX render with `remark-gfm` and `rehype-slug` + `rehype-autolink-headings`.
- Internal cross-references: the agents write links like
  `2026/04/14/blog-foo.md` — we rewrite those to `/p/2026/04/14/blog-foo` at
  render time via a custom `remark` plugin.
- External links: `target="_blank" rel="noopener"`.
- Code blocks: Shiki (`rehype-pretty-code`), one theme (GitHub Light/Dark
  auto).
- `<details>` blocks (other than the frontmatter one we stripped): render
  natively.

### 6.2 Navigation

- Left sidebar (desktop) / drawer (mobile):
  - **Latest** — link to most recent day's README.
  - **Days** — collapsible year/month/day tree built from the manifest.
  - **Topics** — flat list of `topics/*.md`.
  - **Syntheses** — list of `syntheses/**/*.md`.
  - **Search** — button → `/search`.

### 6.3 Search

- Client-side, Fuse.js.
- Index fields: `title`, `topics`, body's first 500 chars (captures TL;DR).
- Loads `/pages.json` (generated at build). For ~hundreds of pages this is
  fine; revisit if it grows past a few thousand.

### 6.4 Rating widget

- Component `<RatingWidget path={page.path} initial={page.userScore} />`.
- 0-10 segmented control. Persists via `POST /api/rate`.
- Optimistic UI with rollback on failure.
- Shows last-saved state; disables while in flight.
- Lives in the right rail (desktop) and in the sticky bottom sheet (mobile).

### 6.5 Chat with page context

- Component `<PageChat page={page} />`.
- New conversation per page load (no cross-page memory).
- Client keeps message list in state; posts to `/api/chat` with `{ messages,
  pagePath }`.
- Server-side: loads the page file fresh (ensures latest content), constructs
  a system prompt:
  > You are assisting the user with follow-up questions on a
  > knowledge-base analysis page. The full page content is below. Answer
  > using this content as primary source; you may use general knowledge to
  > expand or connect. Be direct and concise.
  >
  > ```
  > <page body>
  > ```
- Streams response via Anthropic SDK (`messages.stream`), pipes to client via
  a Server-Sent Events response.
- Model: `claude-opus-4-6` by default, `claude-sonnet-4-6` as a lower-cost
  toggle in UI. Temperature 0.7. `max_tokens: 4096`.

## 7. API Architecture

### 7.1 `POST /api/rate`

Request:
```json
{ "path": "2026/04/14/blog-foo.md", "score": 8 }
```

Server:
1. Auth check: request must include a valid Cloudflare Access JWT header
   (`Cf-Access-Jwt-Assertion`) — Vercel middleware verifies it against
   Cloudflare's JWKS. Rejects any request without one (defense in depth even
   though Access already gates traffic).
2. Validate `path` is inside the whitelisted KB glob; `score` is an integer
   0-10.
3. GET the file from GitHub contents API (to get current SHA + content).
4. Locate the `user_score:` line in the embedded YAML block. Replace it with
   `user_score: <score>`. Only touch that one line.
5. PUT back via contents API with commit message `chore(rating): user_score=<n>
   on <path>` and author `{ name: "kb-reader-app", email: "reader@local" }`.
6. Trigger revalidation for the page's route.
7. Return `{ ok: true, commitSha }`.

Idempotency: if the file's current `user_score` already equals the requested
value, skip the commit and return `{ ok: true, noop: true }`.

### 7.2 `POST /api/chat`

Request:
```json
{
  "pagePath": "2026/04/14/blog-foo.md",
  "model": "claude-opus-4-6",
  "messages": [{ "role": "user", "content": "What's the steelman here?" }]
}
```

Server:
1. Same auth check as rate.
2. Validate `pagePath` whitelisted. Load the file's parsed `body` (fresh
   read).
3. Build system prompt (§6.5).
4. `anthropic.messages.stream({ model, system, messages, max_tokens: 4096 })`.
5. Pipe to SSE response.

Errors: 4xx with `{ error }` for bad input; 502 for Anthropic failures (don't
leak upstream errors).

### 7.3 `POST /api/revalidate`

GitHub webhook target. Payload: push event to `main`.
- Verify `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`.
- For each changed markdown file, call `revalidatePath('/p/…')` and
  `revalidatePath('/day/…')`.
- Also revalidate `/` and `/days`.

This is belt-and-suspenders: Vercel's git integration already redeploys on
push, but ISR + webhook gives snappier refresh for tag-only frontmatter
edits where we skip a full rebuild. **Phase 2** (see §9).

## 8. Security & Privacy

- **Access control**: Cloudflare Access enforces email PIN to the owner's
  email. Vercel deployment URL can be the default `*.vercel.app` or a
  custom domain.
- **Defense in depth**: API routes independently verify the Cloudflare Access
  JWT. If Access is ever misconfigured, the API doesn't open up.
- **Secrets**: all via Vercel env vars, never committed. `GITHUB_TOKEN` is a
  fine-grained PAT scoped to the KB repo with `contents:write` only.
- **Rate limits**: in-memory token bucket on `/api/chat` (20 req/min/IP) — I
  know it's single-user, but cheap insurance if auth ever fails open.
- **Chat privacy**: nothing persisted server-side. Client state lives in the
  tab.
- **CSP**: strict — `default-src 'self'`; allow Anthropic SSE endpoint; no
  inline scripts.

## 9. Phased Scope

### Phase 1 — Reader MVP (ship first)
- Next.js app skeleton in `reader/`.
- Parse + render pages (reader, MDX, internal links, nav).
- Daily/page/topic/synthesis routes.
- Cloudflare Access in front.
- Deploys to Vercel on push.
- **No interactivity yet.** Delete `quartz/` folder.

### Phase 2 — Rating + Chat
- `/api/rate` with GitHub writeback.
- Rating widget UI.
- `/api/chat` with streaming.
- Page chat UI.
- GitHub webhook → `/api/revalidate`.

### Phase 3 — Polish (deferred)
- Search quality (tune Fuse weights, add snippet highlighting).
- Keyboard shortcuts (`j`/`k` navigation, `/` for search).
- Per-topic filtering on day views.
- Export a day to PDF / markdown bundle.

Only Phases 1 and 2 are in scope for this plan. Phase 3 is a backlog marker.

## 10. Success Criteria

Copy this list into the verification rubric in `tasks/todo.md`. Each item is
pass/fail.

### Reading
1. Navigating to `/` renders the most recent day's `README.md` with correct
   MDX.
2. `/day/2026/04/14` renders and links to every `.md` in that folder except
   `run-log-*.md`.
3. `/p/2026/04/14/blog-algorithmicbridge-23-questions-ai` renders title,
   byline, body, and right-rail metadata (topics, relevance, source URL).
4. The embedded frontmatter `<details>` block is NOT visible in the rendered
   body.
5. Internal cross-references in body like `2026/04/14/blog-foo.md` are
   rewritten to clickable `/p/…` links.
6. External links open in a new tab.
7. `/topics/ai-safety-alignment` renders the topic page.
8. `/syntheses` lists all synthesis files.
9. Dark mode honors `prefers-color-scheme`.
10. Mobile viewport (375px) is readable, nav accessible via drawer, no
    horizontal scroll.

### Auth
11. Accessing any route without Cloudflare Access session → redirect to
    Access login.
12. Hitting `/api/rate` or `/api/chat` without the Access JWT header → 401.

### Rating
13. Clicking a 0-10 score on a page issues a commit to the KB repo that
    patches only the `user_score:` line (verified by git diff).
14. Subsequent page load reflects the new score (ISR or webhook
    revalidation).
15. Re-clicking the same score is a no-op (no duplicate commit).
16. Rating a `run-log-*.md` URL returns 400 (not rateable).
17. Invalid score (11, -1, "abc") returns 400.

### Chat
18. Chat widget streams a response; tokens appear incrementally.
19. The page body is included as context (ask "summarize this page" → gets
    page-specific answer, not generic).
20. Chat history resets on page reload.
21. Model toggle (Opus/Sonnet) takes effect on the next message.
22. API key is never present in client bundle or network responses.

### Build / Deploy
23. `npm run build` from `reader/` succeeds with zero TypeScript errors and
    zero lint errors.
24. Git push to `main` triggers a Vercel deploy that finishes green.
25. `pages.json` manifest is generated at build and loaded by the nav/search.

### Edge cases
26. Page with empty `user_score:` field parses correctly (`userScore === null`).
27. Page with `user_score: 0` parses correctly (`userScore === 0`, not null).
28. Page with missing `<details>` block still renders (gracefully degraded
    metadata).
29. A new day folder (created by a future agent run) appears in the nav after
    revalidation without a full rebuild.
30. Two rapid consecutive ratings on the same page don't race — second commit
    supersedes first cleanly.

## 11. Out of Scope (explicitly)

- Multi-user accounts / permissions (single-user tool).
- Writing new pages from the UI (that's the agents' job).
- Editing page body content (we only patch `user_score`).
- Server-side chat history (ephemeral by design).
- Public sharing of pages.
- Comment threads / annotations beyond the single rating field.
- Replacing the existing agents' behavior.

## 12. Resolved Decisions

- **Default chat model**: `claude-opus-4-6`. Sonnet remains as a UI toggle.
- **Chat logging**: NOT persisted. No writes to `_system/chat-log.jsonl`. Chat
  stays ephemeral in the browser tab.
- **Revalidation**: GitHub webhook → `/api/revalidate` is in Phase 2 (not
  deferred). Path-scoped `revalidatePath` beats waiting on full Vercel
  redeploys for tag-only frontmatter edits.
