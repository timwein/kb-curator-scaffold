# Tweet KB Reader

Next.js reader for the self-curating knowledge base. Lives inside the KB
repo at `reader/`; reads markdown from the parent directories at build
time; gated by Cloudflare Access; rating + chat are wired to live APIs.

See `SPEC.md` for the full design. See `tasks/todo.md` for the build
checklist and verification rubric.

---

## Local development

```bash
cd reader
cp .env.local.example .env.local
# fill in .env.local — for local-only dev, the minimum is:
#   SKIP_ACCESS_VERIFY=true
#   ANTHROPIC_API_KEY=sk-ant-…     (only if you want to test chat)
#   GITHUB_TOKEN=ghp_…              (only if you want to test rating)
#   GITHUB_REPO=<your-username>/<your-kb-repo>

npm install
npm run dev
```

Visit <http://localhost:3000>. `predev` runs `sync-content.ts` +
`build-manifest.ts`, which populates `.kb-content/` and
`public/pages.json`. Re-run `npm run manifest` after the KB updates if
you're not running `dev` through that step.

### Useful scripts

| Command           | What it does                                                 |
| ----------------- | ------------------------------------------------------------ |
| `npm run dev`     | Sync content + build manifest + Next dev server.             |
| `npm run build`   | Full production build (same sync + manifest first).          |
| `npm run manifest`| Regenerate `public/pages.json` only.                         |
| `npm run check`   | Run parser fixture + real-content sanity checks.             |
| `npm run lint`    | ESLint.                                                      |

---

## Environment variables

See `.env.local.example`. Required in production:

| Var                       | Purpose                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `GITHUB_REPO`             | `owner/repo` the KB lives in (e.g. `<your-username>/<your-kb-repo>`).   |
| `GITHUB_BRANCH`           | Usually `main`.                                                         |
| `GITHUB_TOKEN`            | Fine-grained PAT, scoped to the KB repo, with `contents: write`.        |
| `GITHUB_WEBHOOK_SECRET`   | Shared secret configured on the GitHub repo webhook.                    |
| `CF_ACCESS_TEAM_DOMAIN`   | Zero Trust team subdomain. Only needed if using Cloudflare Access.      |
| `CF_ACCESS_AUD`           | Application AUD tag from the Access app settings. Only needed if using Cloudflare Access. |
| `ANTHROPIC_API_KEY`       | Server-side only. Used by `/api/chat`.                                  |
| `BASIC_AUTH_USER`         | Username for HTTP Basic Auth gate (simpler alternative to Cloudflare Access). Leave unset to disable. |
| `BASIC_AUTH_PASSWORD`     | Password for HTTP Basic Auth gate. Mark Sensitive.                      |

Dev-only:

| Var                  | Purpose                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| `KB_CONTENT_ROOT`    | Where to read KB content from (default `../`). Override if running the reader outside the KB repo. |
| `SKIP_ACCESS_VERIFY` | Bypass Cloudflare Access JWT verification. **Never set in production.**  |

---

## Deploy to Vercel

1. Create a new Vercel project from this repo.
2. Set **Root Directory** to `reader`.
3. Framework preset: **Next.js** (auto-detected).
4. Build command: `next build` (default).
5. Install command: `npm install` (default).
6. Add the env vars above. Do **not** set `SKIP_ACCESS_VERIFY` or
   `KB_CONTENT_ROOT` — the `.kb-content/` mirror generated at prebuild
   lives inside the project root.
7. Deploy.

### Auth: HTTP Basic Auth (simplest)

Set `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD` in Vercel. `middleware.ts`
gates every request (except `/api/revalidate` for the GitHub webhook) and
the browser shows a native username/password prompt. Credentials are
cached until the tab/window closes. Works without a custom domain.

When Basic Auth is enabled you can leave `CF_ACCESS_*` unset and keep
`SKIP_ACCESS_VERIFY=true`. The middleware is the real gate; the API
routes' Access-JWT check becomes a no-op.

### Cloudflare Access setup (advanced)

1. Open <https://one.dash.cloudflare.com> → your team → **Access →
   Applications → Add an application → Self-hosted**.
2. Application domain: your Vercel hostname (e.g.
   `tweet-kb-reader.vercel.app` or a custom domain if you connect one).
3. Add a **policy**: name "Owner only", Action Allow, Selector Emails,
   value `<your-email>`.
4. In the Access app's settings, grab the **Application Audience (AUD)
   Tag** — paste this as `CF_ACCESS_AUD` in Vercel.
5. Your team domain (the part before `.cloudflareaccess.com`) is
   `CF_ACCESS_TEAM_DOMAIN`.
6. Save. The reader now prompts for a one-time PIN to your email before
   any page or API call lands.

**GitHub webhook bypass.** The `/api/revalidate` endpoint is hit by
GitHub, which cannot authenticate through Access. Add a **second policy**
on the Access app scoped to the path `/api/revalidate`:

- Action: **Bypass**
- Selector: **Everyone** (the HMAC signature on the webhook is the real
  auth for that endpoint)

Alternatively, use a Cloudflare Access **Service Auth** token and
configure GitHub to send it — but HMAC + bypass is simpler.

### GitHub webhook setup

1. On the KB repo → **Settings → Webhooks → Add webhook**.
2. Payload URL: `https://<your-vercel-host>/api/revalidate`
3. Content type: `application/json`
4. Secret: the same string you put in Vercel's `GITHUB_WEBHOOK_SECRET`.
5. Events: **Just the push event**.
6. Active: yes.
7. Save.

Now, every push to `main` (including commits by the managed agents)
fires the webhook, and ISR revalidates the affected paths without
waiting for the full Vercel redeploy.

### GitHub fine-grained PAT

Create at <https://github.com/settings/personal-access-tokens/new>:

- Resource owner: your account
- Expiration: whatever you're comfortable with (90 days is reasonable
  if you don't mind rotating)
- Repository access: **Only select repositories → tweet-knowledge-base**
- Repository permissions: **Contents: Read and write**
- Nothing else.

Paste into Vercel as `GITHUB_TOKEN`.

---

## Architecture at a glance

- **KB content** lives in the parent directory (`2026/`, `topics/`,
  `syntheses/`). The `predev`/`prebuild` step mirrors it into
  `reader/.kb-content/` so Vercel's file tracer picks it up for runtime
  API routes. The mirror is gitignored.
- **Rendering** uses `next-mdx-remote/rsc` with a custom pre-processor
  (`lib/mdx/sanitize.ts`) that escapes stray `<` and `{` in prose so
  content like "<5%" doesn't crash the MDX parser.
- **Internal-link rewriting** (`lib/mdx/rewriteInternalLinks.ts`) resolves
  sibling-relative and `../`-relative markdown links against the source
  file's directory and routes them to the reader's URL scheme.
- **Frontmatter** is parsed from one of two formats: the new
  `<details>`-with-embedded-YAML format, or the older Jekyll-style
  top-of-file `---YAML---` block.
- **Rating** (`/api/rate`) patches only the `user_score:` line in a page's
  frontmatter via the GitHub contents API. Idempotent; retries once on
  SHA conflict; revalidates the affected paths in the same response.
- **Chat** (`/api/chat`) prepends the page body as a system prompt and
  streams via SSE. Per-page, ephemeral, never persisted.
- **Revalidation** (`/api/revalidate`) verifies the GitHub webhook HMAC
  and calls `revalidatePath` for each changed markdown file.

---

## Walking the verification rubric

See `tasks/todo.md` for the full checklist. Items that require a live
deploy + real auth (Access login, rating writeback commits, chat
streaming with a real API key) are called out explicitly — walk them
after the first deploy.
