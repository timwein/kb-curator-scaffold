/**
 * Copy KB markdown content into `reader/.kb-content/` so it lives inside the
 * Next.js project root (required for Vercel/Turbopack file tracing of API
 * routes at runtime). Runs on `prebuild` and `predev`.
 *
 * Source root: KB_CONTENT_ROOT (default `../`).
 * Destination: reader/.kb-content/
 *
 * Preserves directory structure. Copies only `.md` files under the allowed
 * top-level directories. Removes files in the destination that no longer
 * exist in the source.
 */
import fs from "node:fs/promises";
import path from "node:path";
import fg from "fast-glob";

const INCLUDE = [
  "[0-9][0-9][0-9][0-9]/**/*.md",
  "topics/*.md",
  "syntheses/**/*.md",
];
const IGNORE = [
  "_system/**",
  "scripts/**",
  "meta/**",
  "reader/**",
  "quartz/**",
  ".github/**",
  "node_modules/**",
];

function resolveSrc(): string {
  const raw = process.env.KB_CONTENT_ROOT || "../";
  const expanded = raw.startsWith("~/")
    ? path.join(process.env.HOME || "", raw.slice(2))
    : raw;
  return path.resolve(process.cwd(), expanded);
}

async function main() {
  const src = resolveSrc();
  const dst = path.resolve(process.cwd(), ".kb-content");

  const files = await fg(INCLUDE, {
    cwd: src,
    ignore: IGNORE,
    onlyFiles: true,
  });

  // Snapshot existing destination files to detect deletions.
  let existing: string[] = [];
  try {
    existing = await fg(["**/*.md"], { cwd: dst, onlyFiles: true });
  } catch {
    // dst doesn't exist yet; fine.
  }
  const existingSet = new Set(existing);

  let copied = 0;
  let skipped = 0;
  for (const rel of files) {
    const srcPath = path.join(src, rel);
    const dstPath = path.join(dst, rel);
    existingSet.delete(rel);
    const [srcStat, dstStat] = await Promise.all([
      fs.stat(srcPath).catch(() => null),
      fs.stat(dstPath).catch(() => null),
    ]);
    if (!srcStat) continue;
    if (
      dstStat &&
      dstStat.size === srcStat.size &&
      dstStat.mtimeMs >= srcStat.mtimeMs
    ) {
      skipped++;
      continue;
    }
    await fs.mkdir(path.dirname(dstPath), { recursive: true });
    await fs.copyFile(srcPath, dstPath);
    copied++;
  }

  // Remove stale files.
  let removed = 0;
  for (const stale of existingSet) {
    try {
      await fs.unlink(path.join(dst, stale));
      removed++;
    } catch {
      /* ignore */
    }
  }

  console.log(
    `[sync-content] copied=${copied} skipped=${skipped} removed=${removed} total=${files.length} (src=${src})`,
  );
}

main().catch((err) => {
  console.error("[sync-content] failed:", err);
  process.exit(1);
});
