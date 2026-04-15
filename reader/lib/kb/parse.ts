import YAML from "yaml";
import type { KbPage, PageHeader } from "./types";
import { classifyPath, extractDateFromPath } from "./kind";

// Match the first <details>...</details> block. Non-greedy, multiline.
const DETAILS_RE = /<details\b[^>]*>([\s\S]*?)<\/details>/;

// Within a details block, match a ```yaml ... ``` fence.
const YAML_FENCE_RE = /```ya?ml\s*\n([\s\S]*?)\n```/;

// Standard Jekyll-style frontmatter: ---\n ... \n---\n at file start.
const TOP_FRONTMATTER_RE = /^---\s*\n([\s\S]*?)\n---\s*\n?/;

// Markdown H1 title, optionally linked, on first non-blank line.
// Matches: "# Title" OR "# [Title](url)"
const H1_RE = /^#\s+(?:\[([^\]]+)\]\(([^)]+)\)|(.+?))\s*$/m;

// Optional italic byline immediately after H1:  *By ... · ... · 2026-04-14*
const BYLINE_RE = /^\*([^*\n]+)\*\s*$/m;

export interface ParsedPage {
  frontmatter: Record<string, unknown>;
  header: PageHeader;
  body: string; // with details block + leading title/byline stripped
  bodyWithHeader: string; // details block stripped but title+byline preserved (used for MDX render when we want MDX to handle the H1)
}

export function parsePage(raw: string): ParsedPage {
  let frontmatter: Record<string, unknown> = {};
  let bodyWithHeader = raw;

  // Format A: <details> block with ```yaml fence (current/post-2026-04 format).
  const detailsMatch = raw.match(DETAILS_RE);
  if (detailsMatch) {
    const innerYaml = detailsMatch[1].match(YAML_FENCE_RE);
    if (innerYaml) {
      try {
        const parsed = YAML.parse(innerYaml[1]);
        if (parsed && typeof parsed === "object") {
          frontmatter = parsed as Record<string, unknown>;
        }
      } catch {
        // Malformed YAML — fall through with empty frontmatter.
      }
    }
    const start = detailsMatch.index ?? 0;
    const end = start + detailsMatch[0].length;
    let after = bodyWithHeader.slice(end);
    after = after.replace(/^\s*\n+---\s*\n+/, "\n\n");
    after = after.replace(/^\s*\n+/, "\n\n");
    bodyWithHeader = bodyWithHeader.slice(0, start) + after;
  } else {
    // Format B: standard top-of-file ---YAML--- (older tweet format).
    const topMatch = raw.match(TOP_FRONTMATTER_RE);
    if (topMatch) {
      try {
        const parsed = YAML.parse(topMatch[1]);
        if (parsed && typeof parsed === "object") {
          frontmatter = parsed as Record<string, unknown>;
        }
      } catch {
        // Fall through.
      }
      bodyWithHeader = raw.slice(topMatch[0].length).replace(/^\s*\n+/, "");
    }
  }

  // Extract H1 + byline; for old-format tweets with no H1, synthesize from frontmatter.
  const header = extractHeader(bodyWithHeader, frontmatter);
  const body = stripLeadingHeader(bodyWithHeader);
  return { frontmatter, header, body, bodyWithHeader };
}

function extractHeader(
  text: string,
  frontmatter: Record<string, unknown>,
): PageHeader {
  const h1 = text.match(H1_RE);
  let title = "";
  let url: string | undefined;
  if (h1) {
    if (h1[1]) {
      title = h1[1];
      url = h1[2];
    } else if (h1[3]) {
      title = h1[3];
    }
  }
  let byline: string | undefined;
  if (h1) {
    const afterH1 = text.slice((h1.index ?? 0) + h1[0].length);
    const byMatch = afterH1.match(BYLINE_RE);
    if (byMatch && byMatch.index !== undefined && byMatch.index < 120) {
      byline = byMatch[1].trim();
    }
  }
  // Synthesize title from frontmatter for old-format tweets with no H1.
  if (!title) {
    const author = typeof frontmatter["author"] === "string" ? (frontmatter["author"] as string) : undefined;
    const fmTitle = typeof frontmatter["title"] === "string" ? (frontmatter["title"] as string) : undefined;
    const tweetUrl = typeof frontmatter["url"] === "string" ? (frontmatter["url"] as string) : undefined;
    if (fmTitle) {
      title = fmTitle;
    } else if (author) {
      title = `Tweet by ${author}`;
    }
    if (!url && tweetUrl) url = tweetUrl;
    if (!byline && author) {
      const ingested = typeof frontmatter["ingested_at"] === "string" ? (frontmatter["ingested_at"] as string).slice(0, 10) : undefined;
      byline = ingested ? `${author} · ${ingested}` : author;
    }
  }
  return { title: title || "Untitled", url, byline };
}

function stripLeadingHeader(text: string): string {
  // Drop any leading blank lines.
  let out = text.replace(/^\s*\n+/, "");
  // Drop the H1.
  const h1 = out.match(H1_RE);
  if (h1 && h1.index === 0) {
    out = out.slice(h1[0].length).replace(/^\s*\n+/, "");
  }
  // Drop an immediately-following italic byline.
  const byMatch = out.match(BYLINE_RE);
  if (byMatch && byMatch.index === 0) {
    out = out.slice(byMatch[0].length).replace(/^\s*\n+/, "");
  }
  // Drop a leading horizontal rule.
  out = out.replace(/^\s*---\s*\n+/, "");
  return out;
}

function coerceUserScore(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const trimmed = v.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function coerceTopics(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string");
  return [];
}

export function buildKbPage(repoRelPath: string, raw: string): KbPage {
  const parsed = parsePage(raw);
  const kind = classifyPath(repoRelPath);
  const date = extractDateFromPath(repoRelPath);
  const slug = repoRelPath
    .split("/")
    .slice(-1)[0]
    .replace(/\.md$/, "");
  const fm = parsed.frontmatter;
  const userScore = coerceUserScore(fm["user_score"]);
  const relevanceScore = coerceUserScore(fm["relevance_score"]);
  const topics = coerceTopics(fm["topics"]);
  const hasUserScoreField = Object.prototype.hasOwnProperty.call(fm, "user_score");
  const canInteract = hasUserScoreField && kind !== "runlog";

  return {
    path: repoRelPath,
    slug,
    kind,
    date,
    title: parsed.header.title,
    url: parsed.header.url,
    byline: parsed.header.byline,
    frontmatter: fm,
    userScore,
    relevanceScore,
    topics,
    sourceType: typeof fm["source_type"] === "string" ? (fm["source_type"] as string) : undefined,
    slot: typeof fm["slot"] === "string" ? (fm["slot"] as string) : undefined,
    body: parsed.body,
    canRate: canInteract,
    canChat: canInteract,
  };
}
