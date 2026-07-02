import fs from "node:fs/promises";
import path from "node:path";
import fg from "fast-glob";
import { buildKbPage } from "./parse";
import type { KbPage } from "./types";

/**
 * Always resolve to `reader/.kb-content/`, populated by `sync-content.ts` at
 * prebuild/predev. Keeping this a single statically-analyzable path inside the
 * project root lets Vercel's file tracer scope each function bundle to just
 * the markdown it needs — a parent-directory fallback (e.g. `path.resolve(cwd,
 * "../")`) defeats NFT and pulls the entire repo into every traced route.
 */
export function resolveKbRoot(): string {
  return path.resolve(process.cwd(), ".kb-content");
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
