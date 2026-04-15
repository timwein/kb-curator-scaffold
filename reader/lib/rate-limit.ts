/**
 * Naive in-memory token bucket. One Node process per Vercel function
 * instance, so this is best-effort — good enough for a single-user tool
 * behind Cloudflare Access.
 */

interface Bucket {
  tokens: number;
  lastRefill: number;
}

const buckets = new Map<string, Bucket>();
const CAPACITY = 20;
const REFILL_PER_MS = CAPACITY / 60_000; // 20 tokens per 60s

export function allow(key: string): { ok: boolean; retryAfter?: number } {
  const now = Date.now();
  let b = buckets.get(key);
  if (!b) {
    b = { tokens: CAPACITY, lastRefill: now };
    buckets.set(key, b);
  }
  const elapsed = now - b.lastRefill;
  b.tokens = Math.min(CAPACITY, b.tokens + elapsed * REFILL_PER_MS);
  b.lastRefill = now;
  if (b.tokens < 1) {
    const ms = Math.ceil((1 - b.tokens) / REFILL_PER_MS);
    return { ok: false, retryAfter: Math.ceil(ms / 1000) };
  }
  b.tokens -= 1;
  return { ok: true };
}
