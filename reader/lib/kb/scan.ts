import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import fg from "fast-glob";
import { buildKbPage } from "./parse";
import type { KbPage } from "./types";

/**
 * Resolve the absolute filesystem path to the KB content root.
 *
 * - Preferred source: `reader/.kb-content/` (populated by `sync-content.ts`
 *   at prebuild/predev). Living inside the project root lets Vercel's file
 *   tracer pick it up for API routes at runtime.
 * - Fallback: KB_CONTENT_ROOT env var (default `../`), used by the sync
 *   script itself and for direct builds without a sync step.
 */
export function resolveKbRoot(): string {
  const cwd = process.cwd();
  const mirrored = path.resolve(cwd, ".kb-content");
  if (fsSync.existsSync(mirrored)) return mirrored;
  const raw = process.env.KB_CONTENT_ROOT || "../";
  const expanded = raw.startsWith("~/")
    ? path.join(process.env.HOME || "", raw.slice(2))
    : raw;
  return path.resolve(cwd, expanded);
}

const INCLUDE_GLOBS = [
  "[0-9][0-9][0-9][0-9]/**/*.md",
  "topics/*.md",
  "syntheses/**/*.md",
];

const IGNORE_GLOBS = [
  "_system/**",
  "scripts/**",
  "meta/**",
  "reader/**",
  "quartz/**",
  ".github/**",
  "node_modules/**",
];

/** Scan the KB root and return every relevant page as KbPage. */
export async function scanPages(): Promise<KbPage[]> {
  const root = resolveKbRoot();
  const files = await fg(INCLUDE_GLOBS, {
    cwd: root,
    ignore: IGNORE_GLOBS,
    dot: false,
    onlyFiles: true,
  });

  const pages: KbPage[] = [];
  for (const rel of files) {
    try {
      const raw = await fs.readFile(path.join(root, rel), "utf8");
      pages.push(buildKbPage(rel.replace(/\\/g, "/"), raw));
    } catch (err) {
      console.warn(`[scan] failed to read ${rel}:`, (err as Error).message);
    }
  }
  return pages;
}

export async function loadPageByRelPath(relPath: string): Promise<KbPage | null> {
  const root = resolveKbRoot();
  const abs = path.join(root, relPath);
  try {
    const raw = await fs.readFile(abs, "utf8");
    return buildKbPage(relPath.replace(/\\/g, "/"), raw);
  } catch {
    return null;
  }
}
