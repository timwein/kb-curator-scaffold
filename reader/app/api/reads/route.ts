import { NextRequest, NextResponse } from "next/server";
import { verifyAccess } from "@/lib/auth/verifyAccess";
import { getReadPages, setPageRead } from "@/lib/reads";

export const runtime = "nodejs";

function validPagePath(path: string): boolean {
  if (path.includes("..") || path.startsWith("/")) return false;
  if (!path.endsWith(".md")) return false;
  return (
    /^\d{4}\/\d{2}\/\d{2}\//.test(path) ||
    path.startsWith("topics/") ||
    path.startsWith("syntheses/")
  );
}

export async function GET(req: NextRequest) {
  const identity = await verifyAccess(req);
  if (!identity) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const paths = await getReadPages();
  return NextResponse.json({ paths });
}

export async function POST(req: NextRequest) {
  const identity = await verifyAccess(req);
  if (!identity) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: { path?: unknown; read?: unknown };
  try {
    body = (await req.json()) as { path?: unknown; read?: unknown };
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const { path, read } = body;
  if (typeof path !== "string" || typeof read !== "boolean") {
    return NextResponse.json({ error: "invalid fields" }, { status: 400 });
  }
  if (!validPagePath(path)) {
    return NextResponse.json({ error: "invalid path" }, { status: 400 });
  }

  await setPageRead(path, read);
  return NextResponse.json({ ok: true });
}
