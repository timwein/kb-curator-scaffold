/**
 * Surgically replace the `user_score:` line in a KB page's raw content with
 * a new integer value. Only the first occurrence inside the embedded
 * <details> YAML block is touched.
 *
 * - If no `user_score:` line exists → throw (we shouldn't be rating such a page).
 * - If the current value already equals the new score → return same string
 *   (idempotent; caller detects via reference equality and skips the commit).
 * - Preserves original indentation.
 */

export class MissingUserScoreField extends Error {
  constructor() {
    super("user_score field not found in page");
    this.name = "MissingUserScoreField";
  }
}

const TIM_SCORE_LINE_RE = /^(\s*)user_score\s*:\s*(.*?)\s*$/m;

export interface PatchResult {
  content: string;
  changed: boolean;
}

export function patchUserScore(raw: string, score: number): PatchResult {
  if (!Number.isInteger(score) || score < 0 || score > 10) {
    throw new RangeError("score must be integer 0..10");
  }
  const m = raw.match(TIM_SCORE_LINE_RE);
  if (!m) throw new MissingUserScoreField();
  const [whole, indent, currentValueRaw] = m;
  const currentTrimmed = currentValueRaw.trim();
  const newValue = String(score);

  // Idempotency: current value (parsed) equals new.
  if (currentTrimmed !== "" && Number(currentTrimmed) === score) {
    return { content: raw, changed: false };
  }

  const replacement = `${indent}user_score: ${newValue}`;
  const idx = m.index ?? raw.indexOf(whole);
  const content = raw.slice(0, idx) + replacement + raw.slice(idx + whole.length);
  return { content, changed: true };
}
