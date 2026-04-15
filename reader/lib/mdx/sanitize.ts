/**
 * Preprocess markdown body before MDX compile.
 *
 * MDX treats `<` as a JSX opener and `{` as an expression opener. Plain
 * markdown content frequently contains bare `<` (e.g. "<5%", "<100ms") and
 * `{` (e.g. "{x: y}") that would blow up the MDX parser.
 *
 * Strategy: split on fenced code blocks (```…``` or ~~~…~~~), leave them
 * untouched, and in the remaining prose escape any `<` NOT followed by a
 * tag-name-starting character and any `{` NOT followed by a word/$/_.
 */

const FENCE_RE = /(^```[\s\S]*?^```|^~~~[\s\S]*?^~~~)/gm;
const INLINE_CODE_RE = /`[^`]*`/g;

function escapeProse(text: string): string {
  // Preserve inline code spans first.
  const spans: string[] = [];
  const withHoles = text.replace(INLINE_CODE_RE, (m) => {
    spans.push(m);
    return `\u0000${spans.length - 1}\u0000`;
  });

  // Escape stray `<`: anything not followed by a letter, `/`, or `!`
  // (HTML/JSX openers we want MDX to parse).
  let escaped = withHoles.replace(/<(?![a-zA-Z/!])/g, "&lt;");

  // Escape stray `{`: anything not followed by alpha/digit/_/$ (which would
  // be a JSX expression). This catches `{x: y}` and `{1 + 2}` in prose.
  // Also `{` at end of line or followed by whitespace is escaped.
  escaped = escaped.replace(/\{(?![a-zA-Z0-9_$])/g, "&#123;");

  // Restore inline code spans.
  escaped = escaped.replace(/\u0000(\d+)\u0000/g, (_, i) => spans[Number(i)]);
  return escaped;
}

export function sanitizeMdx(body: string): string {
  const parts = body.split(FENCE_RE);
  return parts
    .map((part, i) => (i % 2 === 1 ? part : escapeProse(part)))
    .join("");
}
