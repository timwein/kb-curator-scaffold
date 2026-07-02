import { Redis } from "@upstash/redis";

const KEY = "reader:read-pages";

// Upstash is optional: when KV_REST_API_URL/TOKEN aren't set, cross-device
// read-state sync is disabled and the client falls back to its localStorage
// cache. We resolve the client lazily and remember the "unconfigured" state
// so we don't re-check env on every request.
let client: Redis | null = null;
let resolved = false;

function getRedis(): Redis | null {
  if (resolved) return client;
  resolved = true;
  const url = process.env.KV_REST_API_URL;
  const token = process.env.KV_REST_API_TOKEN;
  if (!url || !token) return null;
  client = new Redis({ url, token });
  return client;
}

export async function getReadPages(): Promise<string[]> {
  const r = getRedis();
  if (!r) return [];
  const members = await r.smembers(KEY);
  return members as string[];
}

export async function setPageRead(path: string, read: boolean): Promise<void> {
  const r = getRedis();
  if (!r) return;
  if (read) {
    await r.sadd(KEY, path);
  } else {
    await r.srem(KEY, path);
  }
}
