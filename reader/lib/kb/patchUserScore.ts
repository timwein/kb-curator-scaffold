/**
 * Update `user_score` on a KB page's raw content.
 *
 * - If a `user_score:` line exists → replace its value, preserving indent.
 * - If no `user_score:` line exists → insert one at the end of the embedded
 *   YAML frontmatter block (either inside the ```yaml fence under <details>
 *   or inside the top-of-file `---` frontmatter).
 * - Idempotent: if the existing value already equals the requested score,
 *   returns unchanged.
 *
 * Throws `MissingUserScoreField` only when there's nowhere sane to insert
 * (no frontmatter block at all).
 */

export class MissingUserScoreField extends Error {
  constructor() {
    super("no frontmatter block found in page");
    this.name = "MissingUserScoreField";
  }
}

const USER_SCORE_LINE_RE = /^(\s*)user_score\s*:\s*(.*?)\s*$/m;
const YAML_FENCE_RE = /```ya?ml\s*\n([\s\S]*?)\n```/;
const TOP_FRONTMATTER_RE = /^---\s*\n([\s\S]*?)\n---\s*\n?/;

export interface PatchResult {
  content: string;
  changed: boolean;
}

export function patchUserScore(raw: string, score: number): PatchResult {
  if (!Number.isInteger(score) || score < 0 || score > 10) {
    throw new RangeError("score must be integer 0..10");
  }
  const newValue = String(score);

  const existing = raw.match(USER_SCORE_LINE_RE);
  if (existing) {
    const [whole, indent, currentValueRaw] = existing;
    const currentTrimmed = currentValueRaw.trim();
    if (currentTrimmed !== "" && Number(currentTrimmed) === score) {
      return { content: raw, changed: false };
    }
    const replacement = `${indent}user_score: ${newValue}`;
    const idx = existing.index ?? raw.indexOf(whole);
    const content =
      raw.slice(0, idx) + replacement + raw.slice(idx + whole.length);
    return { content, changed: true };
  }

  // No existing line — insert at the end of the YAML frontmatter block.
  const fence = raw.match(YAML_FENCE_RE);
  if (fence) {
    const inner = fence[1];
    const newInner = `${inner}\nuser_score: ${newValue}`;
    const idx = fence.index ?? raw.indexOf(fence[0]);
    const rebuilt = fence[0].replace(inner, newInner);
    const content = raw.slice(0, idx) + rebuilt + raw.slice(idx + fence[0].length);
    return { content, changed: true };
  }

  const top = raw.match(TOP_FRONTMATTER_RE);
  if (top) {
    const inner = top[1];
    const newInner = `${inner}\nuser_score: ${newValue}`;
    const rebuilt = top[0].replace(inner, newInner);
    const content = rebuilt + raw.slice(top[0].length);
    return { content, changed: true };
  }

  throw new MissingUserScoreField();
}
