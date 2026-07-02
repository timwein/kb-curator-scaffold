import { NextRequest, NextResponse } from "next/server";
import { Octokit } from "@octokit/rest";
import { verifyAccess } from "@/lib/auth/verifyAccess";

export const runtime = "nodejs";

interface AnalyzeBody {
  url: string;
}

interface ParsedRepo {
  owner: string;
  repo: string;
}

function parseRepo(): ParsedRepo {
  const raw = process.env.GITHUB_REPO;
  if (!raw) throw new Error("GITHUB_REPO not set (expected 'owner/repo')");
  const [owner, repo] = raw.split("/");
  if (!owner || !repo) throw new Error(`GITHUB_REPO malformed: ${raw}`);
  return { owner, repo };
}

/**
 * Validates a user-supplied URL before we hand it to the agent workflow.
 *
 * - Must parse as a URL.
 * - Must use http or https (no javascript:, file:, data:, etc.).
 * - Must be ≤ 2000 chars.
 * - Must have a hostname with a dot (filters out localhost / raw hostnames
 *   that would waste an agent run).
 * - Strips any control characters as defense-in-depth against prompt-injection
 *   into the kickoff message; if any were found, rejects the URL.
 */
function validateUrl(raw: unknown): { ok: true; url: string } | { ok: false; error: string } {
  if (typeof raw !== "string") return { ok: false, error: "url must be a string" };
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, error: "url is required" };
  if (trimmed.length > 2000) return { ok: false, error: "url too long (max 2000 chars)" };

  // Reject any control char (newlines, tabs, NUL, etc.) — prevents a user
  // from injecting extra instructions into the agent's kickoff message.
  if (/[\x00-\x1f\x7f]/.test(trimmed)) {
    return { ok: false, error: "url contains control characters" };
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { ok: false, error: "url is not a valid URL" };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, error: "url must use http or https" };
  }
  if (!parsed.hostname.includes(".")) {
    return { ok: false, error: "url must have a valid hostname" };
  }

  return { ok: true, url: trimmed };
}

export async function POST(req: NextRequest) {
  const identity = await verifyAccess(req);
  if (!identity) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: AnalyzeBody;
  try {
    body = (await req.json()) as AnalyzeBody;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const check = validateUrl(body?.url);
  if (!check.ok) {
    return NextResponse.json({ error: check.error }, { status: 400 });
  }
  const url = check.url;

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "server misconfigured (GITHUB_TOKEN)" },
      { status: 500 },
    );
  }

  let owner: string;
  let repo: string;
  try {
    ({ owner, repo } = parseRepo());
  } catch (err) {
    const msg = err instanceof Error ? err.message : "bad GITHUB_REPO";
    return NextResponse.json({ error: msg }, { status: 500 });
  }

  const branch = process.env.GITHUB_BRANCH || "main";
  const workflow_id = "blog-ingest.yml";
  const octokit = new Octokit({ auth: token });

  // Dispatch the existing workflow with the URL as an input. The workflow
  // picks this up as `SINGLE_URL` env var and run-blog-ingest.py swaps in
  // the single-URL kickoff message for the agent.
  //
  // Timestamp captured BEFORE dispatch so we can reliably find the new run
  // below — dispatch returns 204 with no body, so we need to query for it.
  const dispatchedAt = new Date();
  try {
    await octokit.actions.createWorkflowDispatch({
      owner,
      repo,
      workflow_id,
      ref: branch,
      inputs: { url, slot: "manual" },
    });
  } catch (err: unknown) {
    const e = err as { status?: number; message?: string };
    // 403 = token lacks actions:write; 404 = workflow file missing.
    if (e.status === 403) {
      return NextResponse.json(
        { error: "github token lacks actions:write scope" },
        { status: 500 },
      );
    }
    if (e.status === 404) {
      return NextResponse.json(
        { error: `workflow ${workflow_id} not found on ${owner}/${repo}@${branch}` },
        { status: 500 },
      );
    }
    return NextResponse.json(
      { error: e.message || "failed to dispatch workflow" },
      { status: 502 },
    );
  }

  // The dispatch API returns 204 with no body, so we poll for the newly-
  // created run to return its URL to the client. Up to ~3s total — if we
  // don't find it, return success without the run URL (the user can still
  // see it in the Actions tab).
  const workflowsListUrl = `https://github.com/${owner}/${repo}/actions/workflows/${workflow_id}`;
  let runUrl: string | null = null;
  let runId: number | null = null;
  const maxAttempts = 6;
  const delayMs = 500;
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((r) => setTimeout(r, delayMs));
    try {
      const { data } = await octokit.actions.listWorkflowRuns({
        owner,
        repo,
        workflow_id,
        event: "workflow_dispatch",
        per_page: 5,
      });
      // Find the first run that started at or after our dispatch time.
      const match = data.workflow_runs.find((run) => {
        const created = new Date(run.created_at);
        return created.getTime() >= dispatchedAt.getTime() - 1000;
      });
      if (match) {
        runUrl = match.html_url;
        runId = match.id;
        break;
      }
    } catch {
      // Non-fatal — keep trying.
    }
  }

  return NextResponse.json({
    ok: true,
    url,
    runUrl: runUrl ?? workflowsListUrl,
    runId,
    fallback: runUrl === null,
  });
}
