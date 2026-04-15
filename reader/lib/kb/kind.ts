import type { PageKind } from "./types";

/**
 * Classify a repo-relative path into a PageKind.
 * Path is forward-slash normalized (no leading slash).
 *
 * Order matters: runlog must win over blog/tweet when filename starts with
 * `run-log-` even under a date folder.
 */
export function classifyPath(path: string): PageKind {
  const parts = path.split("/");
  const filename = parts[parts.length - 1];
  const stem = filename.replace(/\.md$/, "");

  if (stem.startsWith("run-log-")) return "runlog";
  if (filename === "README.md") {
    // README under a YYYY/MM/DD folder is the daily landing.
    // A top-level README would be root — classify as "other".
    if (parts.length >= 4 && /^\d{4}$/.test(parts[0])) return "daily";
    return "other";
  }
  if (parts[0] === "syntheses" || /-synthesis(-|\.)/.test(stem)) return "synthesis";
  if (parts[0] === "topics") return "topic";
  if (stem.startsWith("blog-")) return "blog";
  if (/^\d+-/.test(stem)) return "tweet";
  return "other";
}

/** Extract ISO date (YYYY-MM-DD) from a YYYY/MM/DD/... path, else null. */
export function extractDateFromPath(path: string): string | null {
  const m = path.match(/^(\d{4})\/(\d{2})\/(\d{2})\//);
  if (!m) return null;
  return `${m[1]}-${m[2]}-${m[3]}`;
}
