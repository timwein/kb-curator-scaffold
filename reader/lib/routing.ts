import type { ManifestEntry } from "./kb/types";

/** Compute the in-app href for a manifest entry. */
export function hrefForEntry(entry: ManifestEntry): string {
  if (entry.kind === "topic") return `/topics/${entry.slug}`;
  // Synthesis files inside the top-level `syntheses/` tree go to /syntheses/...
  if (entry.path.startsWith("syntheses/")) {
    return "/" + entry.path.replace(/\.md$/, "");
  }
  // Daily landing: /day/yyyy/mm/dd
  if (entry.kind === "daily" && entry.date) {
    const [y, m, d] = entry.date.split("-");
    return `/day/${y}/${m}/${d}`;
  }
  // Any other dated page (blog, tweet, synthesis-in-day-folder) → /p/...
  if (entry.date) {
    const [y, m, d] = entry.date.split("-");
    return `/p/${y}/${m}/${d}/${entry.slug}`;
  }
  return "/";
}
