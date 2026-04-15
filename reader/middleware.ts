import { NextResponse, type NextRequest } from "next/server";

/**
 * HTTP Basic Auth gate for the whole site.
 *
 * - Checks `Authorization: Basic <base64(user:pass)>` against the
 *   `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` env vars.
 * - If creds match → pass through.
 * - If creds missing/wrong → 401 with `WWW-Authenticate: Basic`, which
 *   triggers the browser's native username/password prompt.
 * - If `BASIC_AUTH_USER` or `BASIC_AUTH_PASSWORD` isn't set, the gate is
 *   disabled (useful for `next dev` locally). Deploy without setting these
 *   and the site will be public, so set them in Vercel production env.
 *
 * `/api/revalidate` is excluded in the matcher so GitHub's webhook (which
 * can't send Basic Auth) still works — the HMAC signature is the real auth
 * for that endpoint.
 */
export function middleware(req: NextRequest) {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASSWORD;

  if (!user || !pass) {
    return NextResponse.next();
  }

  const header = req.headers.get("authorization");
  if (header?.startsWith("Basic ")) {
    const encoded = header.slice(6).trim();
    try {
      const decoded = atob(encoded);
      const sepIdx = decoded.indexOf(":");
      if (sepIdx >= 0) {
        const u = decoded.slice(0, sepIdx);
        const p = decoded.slice(sepIdx + 1);
        if (timingSafeEqual(u, user) && timingSafeEqual(p, pass)) {
          return NextResponse.next();
        }
      }
    } catch {
      // malformed base64 → fall through to 401
    }
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="KB Reader", charset="UTF-8"',
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export const config = {
  // Match everything EXCEPT the GitHub webhook endpoint and Next.js static
  // assets. Everything else (pages, /api/rate, /api/chat, pages.json,
  // robots.txt) is gated.
  matcher: ["/((?!api/revalidate|_next/static|_next/image|favicon.ico).*)"],
};
