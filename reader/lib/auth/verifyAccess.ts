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

/**
 * Verifies the Cloudflare Access JWT on an API request.
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
