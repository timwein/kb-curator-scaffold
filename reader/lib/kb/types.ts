export type PageKind =
  | "blog"
  | "tweet"
  | "synthesis"
  | "daily"
  | "topic"
  | "runlog"
  | "other";

export interface PageHeader {
  title: string;
  url?: string;
  byline?: string;
}

export interface KbPage {
  // identity
  path: string; // repo-relative, e.g. "2026/04/14/blog-foo.md"
  slug: string; // filename stem, e.g. "blog-foo"
  kind: PageKind;
  date: string | null; // ISO "2026-04-14" if under YYYY/MM/DD
  // display
  title: string;
  url?: string;
  byline?: string;
  // frontmatter
  frontmatter: Record<string, unknown>;
  userScore: number | null;
  relevanceScore: number | null;
  topics: string[];
  sourceType?: string;
  slot?: string;
  // body (with <details> block stripped)
  body: string;
  // interactive eligibility
  canRate: boolean;
  canChat: boolean;
}

export interface ManifestEntry {
  path: string;
  slug: string;
  kind: PageKind;
  date: string | null;
  title: string;
  topics: string[];
  userScore: number | null;
  preview: string; // 500-char excerpt for search
}
