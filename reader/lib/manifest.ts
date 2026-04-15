import fs from "node:fs/promises";
import path from "node:path";
import type { ManifestEntry } from "./kb/types";

let cached: ManifestEntry[] | null = null;

export async function loadManifest(): Promise<ManifestEntry[]> {
  if (cached) return cached;
  const p = path.resolve(process.cwd(), "public/pages.json");
  try {
    const raw = await fs.readFile(p, "utf8");
    cached = JSON.parse(raw) as ManifestEntry[];
    return cached;
  } catch {
    return [];
  }
}

export function clearManifestCache() {
  cached = null;
}
