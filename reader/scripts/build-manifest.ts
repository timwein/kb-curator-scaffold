import fs from "node:fs/promises";
import path from "node:path";
import { scanPages } from "../lib/kb/scan";
import type { ManifestEntry } from "../lib/kb/types";

async function main() {
  const pages = await scanPages();
  const manifest: ManifestEntry[] = pages.map((p) => ({
    path: p.path,
    slug: p.slug,
    kind: p.kind,
    date: p.date,
    title: p.title,
    topics: p.topics,
    userScore: p.userScore,
    preview: p.body.slice(0, 500),
  }));

  // Sort: dated pages newest first, then topics/syntheses alphabetically.
  manifest.sort((a, b) => {
    if (a.date && b.date) return b.date.localeCompare(a.date);
    if (a.date) return -1;
    if (b.date) return 1;
    return a.path.localeCompare(b.path);
  });

  const outDir = path.resolve(process.cwd(), "public");
  await fs.mkdir(outDir, { recursive: true });
  const outFile = path.join(outDir, "pages.json");
  await fs.writeFile(outFile, JSON.stringify(manifest), "utf8");

  const byKind = manifest.reduce<Record<string, number>>((acc, p) => {
    acc[p.kind] = (acc[p.kind] || 0) + 1;
    return acc;
  }, {});
  console.log(
    `[manifest] wrote ${manifest.length} entries to ${outFile} (${Object.entries(
      byKind,
    )
      .map(([k, v]) => `${k}=${v}`)
      .join(", ")})`,
  );
}

main().catch((err) => {
  console.error("[manifest] failed:", err);
  process.exit(1);
});
