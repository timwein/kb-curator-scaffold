import type { ManifestEntry } from "./types";

/**
 * When a day has no curated README.md yet, synthesize one from the manifest
 * so the day view matches the look of curated days (sections by source type,
 * tables with scores, synthesis links, run logs).
 */
export function buildSyntheticReadme(pagesOfDay: ManifestEntry[]): string {
  const blogPages = pagesOfDay.filter((p) => p.kind === "blog");
  const tweetPages = pagesOfDay.filter((p) => p.kind === "tweet");
  const podcastPages = pagesOfDay.filter((p) => p.kind === "podcast");
  const syntheses = pagesOfDay.filter((p) => p.kind === "synthesis");
  const runlogs = pagesOfDay.filter((p) => p.kind === "runlog");

  const sections: string[] = [];

  const blogSyntheses = syntheses.filter((p) => p.slug.startsWith("blog-"));
  if (blogPages.length > 0 || blogSyntheses.length > 0) {
    const lines: string[] = ["## Blog Curator", ""];
    for (const s of blogSyntheses) {
      lines.push(`**→ [${s.title}](${s.slug}.md)** ← start here`);
      lines.push("");
    }
    if (blogPages.length > 0) {
      lines.push("| # | Article | Score |");
      lines.push("|---|---------|-------|");
      blogPages
        .slice()
        .sort((a, b) => (b.userScore ?? -1) - (a.userScore ?? -1))
        .forEach((p, i) => {
          lines.push(
            `| ${i + 1} | [${p.title}](${p.slug}.md) | ${p.userScore ?? "—"} |`,
          );
        });
    }
    sections.push(lines.join("\n"));
  }

  if (tweetPages.length > 0) {
    const lines: string[] = ["## Tweet Agent", ""];
    lines.push("| # | Analysis | Author |");
    lines.push("|---|----------|--------|");
    tweetPages.forEach((p, i) => {
      const parts = p.slug.split("-");
      const handle = parts[1] ? `@${parts[1]}` : "";
      const cleanTitle = p.title.replace(/^@[^:]+:\s*/, "").trim() || p.title;
      lines.push(`| ${i + 1} | [${cleanTitle}](${p.slug}.md) | ${handle} |`);
    });
    sections.push(lines.join("\n"));
  }

  const podcastSyntheses = syntheses.filter((p) =>
    p.slug.startsWith("podcast-"),
  );
  if (podcastPages.length > 0 || podcastSyntheses.length > 0) {
    const lines: string[] = ["## Podcast Curator", ""];
    for (const s of podcastSyntheses) {
      lines.push(`**→ [${s.title}](${s.slug}.md)** ← start here`);
      lines.push("");
    }
    if (podcastPages.length > 0) {
      lines.push("| # | Episode | Score |");
      lines.push("|---|---------|-------|");
      podcastPages
        .slice()
        .sort((a, b) => (b.userScore ?? -1) - (a.userScore ?? -1))
        .forEach((p, i) => {
          lines.push(
            `| ${i + 1} | [${p.title}](${p.slug}.md) | ${p.userScore ?? "—"} |`,
          );
        });
    }
    sections.push(lines.join("\n"));
  }

  if (runlogs.length > 0) {
    const lines: string[] = ["## Run Logs", ""];
    for (const p of runlogs) {
      lines.push(`- [${p.title}](${p.slug}.md)`);
    }
    sections.push(lines.join("\n"));
  }

  return sections.join("\n\n---\n\n");
}
