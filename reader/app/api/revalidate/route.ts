import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";
import { revalidatePath } from "next/cache";

export const runtime = "nodejs";

interface PushPayload {
  ref?: string;
  commits?: Array<{
    added?: string[];
    modified?: string[];
    removed?: string[];
  }>;
}

function verifySignature(rawBody: string, signature: string | null): boolean {
  const secret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!secret || !signature) return false;
  const expected =
    "sha256=" +
    crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(signature);
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

export async function POST(req: NextRequest) {
  const raw = await req.text();
  const sig = req.headers.get("x-hub-signature-256");
  if (!verifySignature(raw, sig)) {
    return NextResponse.json({ error: "bad signature" }, { status: 401 });
  }

  const event = req.headers.get("x-github-event");
  if (event !== "push") {
    return NextResponse.json({ ok: true, skipped: event });
  }

  let payload: PushPayload;
  try {
    payload = JSON.parse(raw) as PushPayload;
  } catch {
    return NextResponse.json({ error: "bad body" }, { status: 400 });
  }

  const branch = process.env.GITHUB_BRANCH || "main";
  if (payload.ref && payload.ref !== `refs/heads/${branch}`) {
    return NextResponse.json({ ok: true, skipped: "non-main" });
  }

  const touched = new Set<string>();
  for (const c of payload.commits || []) {
    for (const f of [
      ...(c.added || []),
      ...(c.modified || []),
      ...(c.removed || []),
    ]) {
      touched.add(f);
    }
  }

  const paths = new Set<string>();
  paths.add("/");
  paths.add("/days");
  paths.add("/topics");
  paths.add("/syntheses");

  for (const f of touched) {
    if (!f.endsWith(".md")) continue;
    const dateMatch = f.match(/^(\d{4})\/(\d{2})\/(\d{2})\/(.+)\.md$/);
    if (dateMatch) {
      const [, y, m, d, rest] = dateMatch;
      if (rest === "README") {
        paths.add(`/day/${y}/${m}/${d}`);
      } else {
        paths.add(`/p/${y}/${m}/${d}/${rest}`);
        paths.add(`/day/${y}/${m}/${d}`);
      }
      continue;
    }
    const topicMatch = f.match(/^topics\/(.+)\.md$/);
    if (topicMatch) {
      paths.add(`/topics/${topicMatch[1]}`);
      continue;
    }
    if (f.startsWith("syntheses/")) {
      paths.add("/" + f.replace(/\.md$/, ""));
    }
  }

  for (const p of paths) {
    try {
      revalidatePath(p);
    } catch {
      // Best-effort.
    }
  }

  return NextResponse.json({ ok: true, revalidated: [...paths] });
}
