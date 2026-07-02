import { jwtVerify, createRemoteJWKSet } from "jose";
import type { NextRequest } from "next/server";

const ACCESS_HEADER = "cf-access-jwt-assertion";

export interface AccessIdentity {
  email: string;
  sub: string;
}

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function getJwks() {
  if (jwks) return jwks;
  const domain = process.env.CF_ACCESS_TEAM_DOMAIN;
  if (!domain) throw new Error("CF_ACCESS_TEAM_DOMAIN not set");
  const url = new URL(
    `https://${domain}.cloudflareaccess.com/cdn-cgi/access/certs`,
  );
  jwks = createRemoteJWKSet(url, { cooldownDuration: 30_000 });
  return jwks;
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

function verifyBasicAuth(req: NextRequest): AccessIdentity | null {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASSWORD;
  if (!user || !pass) return null;
  const header = req.headers.get("authorization");
  if (!header?.startsWith("Basic ")) return null;
  try {
    const decoded = atob(header.slice(6).trim());
    const sepIdx = decoded.indexOf(":");
    if (sepIdx < 0) return null;
    const u = decoded.slice(0, sepIdx);
    const p = decoded.slice(sepIdx + 1);
    if (timingSafeEqual(u, user) && timingSafeEqual(p, pass)) {
      return { email: user, sub: `basic:${user}` };
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Verifies the request identity on an API call. Accepts either Cloudflare
 * Access JWT (when CF_ACCESS_* is configured) or HTTP Basic Auth (when
 * BASIC_AUTH_USER/PASSWORD is configured). Whichever gate the site is using
 * in production, the API trusts the same credentials.
 *
 * Returns the identity on success, or null on failure (caller should 401).
 *
 * In dev, setting `SKIP_ACCESS_VERIFY=true` bypasses verification and
 * returns a fake identity — never set this in production.
 */
export async function verifyAccess(
  req: NextRequest,
): Promise<AccessIdentity | null> {
  if (process.env.SKIP_ACCESS_VERIFY === "true") {
    return { email: "dev@local", sub: "dev-local" };
  }

  const basic = verifyBasicAuth(req);
  if (basic) return basic;

  const token = req.headers.get(ACCESS_HEADER);
  if (!token) return null;
  try {
    const aud = process.env.CF_ACCESS_AUD;
    if (!aud) throw new Error("CF_ACCESS_AUD not set");
    const { payload } = await jwtVerify(token, getJwks(), {
      audience: aud,
    });
    const email = typeof payload.email === "string" ? payload.email : null;
    const sub = typeof payload.sub === "string" ? payload.sub : null;
    if (!email || !sub) return null;
    return { email, sub };
  } catch {
    return null;
  }
}
