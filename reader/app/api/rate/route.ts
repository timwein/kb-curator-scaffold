import { NextRequest, NextResponse } from "next/server";
import { Octokit } from "@octokit/rest";
import { Buffer } from "node:buffer";
import { revalidatePath } from "next/cache";
import { verifyAccess } from "@/lib/auth/verifyAccess";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { patchUserScore, MissingUserScoreField } from "@/lib/kb/patchUserScore";

export const runtime = "nodejs";

interface RateBody {
  path: string;
  score: number;
}

function parseRepo(): { owner: string; repo: string } {
  const raw = process.env.GITHUB_REPO;
  if (!raw) throw new Error("GITHUB_REPO not set (expected 'owner/repo')");
  const [owner, repo] = raw.split("/");
  if (!owner || !repo) throw new Error(`GITHUB_REPO malformed: ${raw}`);
  return { owner, repo };
}

function validPagePath(path: string): boolean {
  if (path.includes("..") || path.startsWith("/")) return false;
  if (!path.endsWith(".md")) return false;
  // Only allow content under dated folders, topics/, or syntheses/.
  return (
    /^\d{4}\/\d{2}\/\d{2}\//.test(path) ||
    path.startsWith("topics/") ||
    path.startsWith("syntheses/")
  );
}

export async function POST(req: NextRequest) {
  const identity = await verifyAccess(req);
  if (!identity) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: RateBody;
  try {
    body = (await req.json()) as RateBody;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const { path, score } = body;
  if (typeof path !== "string" || typeof score !== "number") {
    return NextResponse.json({ error: "invalid fields" }, { status: 400 });
  }
  if (!Number.isInteger(score) || score < 0 || score > 10) {
    return NextResponse.json(
      { error: "score must be integer 0..10" },
      { status: 400 },
    );
  }
  if (!validPagePath(path)) {
    return NextResponse.json({ error: "invalid path" }, { status: 400 });
  }

  // Confirm the page exists and is rate-eligible.
  const page = await loadPageByRelPath(path);
  if (!page) {
    return NextResponse.json({ error: "page not found" }, { status: 404 });
  }
  if (!page.canRate) {
    return NextResponse.json({ error: "page not rateable" }, { status: 400 });
  }

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "server misconfigured (GITHUB_TOKEN)" },
      { status: 500 },
    );
  }
  const { owner, repo } = parseRepo();
  const branch = process.env.GITHUB_BRANCH || "main";
  const octokit = new Octokit({ auth: token });

  // GET → patch → PUT with one retry on 409.
  for (let attempt = 0; attempt < 2; attempt++) {
    const { data: getData } = await octokit.repos.getContent({
      owner,
      repo,
      path,
      ref: branch,
    });
    if (Array.isArray(getData) || getData.type !== "file") {
      return NextResponse.json({ error: "not a file" }, { status: 400 });
    }
    const currentRaw = Buffer.from(
      getData.content,
      getData.encoding as BufferEncoding,
    ).toString("utf8");

    let patched;
    try {
      patched = patchUserScore(currentRaw, score);
    } catch (err) {
      if (err instanceof MissingUserScoreField) {
        return NextResponse.json(
          { error: "page has no user_score field" },
          { status: 400 },
        );
      }
      return NextResponse.json(
        { error: "patch failed" },
        { status: 500 },
      );
    }

    if (!patched.changed) {
      return NextResponse.json({ ok: true, noop: true });
    }

    try {
      const { data: putData } = await octokit.repos.createOrUpdateFileContents(
        {
          owner,
          repo,
          path,
          branch,
          message: `chore(rating): user_score=${score} on ${path}`,
          content: Buffer.from(patched.content, "utf8").toString("base64"),
          sha: getData.sha,
          committer: {
            name: "tim-reader-app",
            email: "tim-reader-app@users.noreply.github.com",
          },
          author: {
            name: "tim-reader-app",
            email: "tim-reader-app@users.noreply.github.com",
          },
        },
      );
      const commitSha = putData.commit.sha;

      // Revalidate affected ISR paths.
      const dateMatch = path.match(/^(\d{4})\/(\d{2})\/(\d{2})\/(.+)\.md$/);
      if (dateMatch) {
        const [, y, m, d, rest] = dateMatch;
        revalidatePath(`/p/${y}/${m}/${d}/${rest}`);
        revalidatePath(`/day/${y}/${m}/${d}`);
      }
      revalidatePath("/");

      return NextResponse.json({ ok: true, commitSha });
    } catch (err: unknown) {
      const e = err as { status?: number; message?: string };
      if (e.status === 409 && attempt === 0) {
        // SHA conflict: refetch and retry.
        continue;
      }
      return NextResponse.json(
        { error: e.message || "github write failed" },
        { status: 502 },
      );
    }
  }

  return NextResponse.json(
    { error: "exhausted retries" },
    { status: 502 },
  );
}
