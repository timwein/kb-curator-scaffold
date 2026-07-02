import type { Plugin } from "unified";
import type { Root, InlineCode, Link } from "mdast";
import { visit, SKIP } from "unist-util-visit";

/**
 * Remark plugin that turns inline-code references to other KB pages into
 * clickable links. Authors write paths like `kb/topics/foo.md` or
 * `kb/2026/05/15/blog-foo.md` in backticks; without this plugin those render
 * as plain monospace text. The plugin wraps the inline-code node in a link
 * pointing at the reader's route, preserving the monospace styling so the
 * reference still reads as a path.
 *
 * Recognized patterns (with or without a leading `kb/` prefix):
 *   topics/<name>.md            -> /topics/<name>
 *   syntheses/<path>.md         -> /syntheses/<path>
 *   <YYYY>/<MM>/<DD>/<slug>.md  -> /p/<YYYY>/<MM>/<DD>/<slug>
 *   <YYYY>/<MM>/<DD>/README.md  -> /day/<YYYY>/<MM>/<DD>
 *
 * Anything else (e.g. `kb/analyses/...`, paths missing `.md`, partial refs
 * like `kb/2026/05/15/...`) is left untouched.
 */
const KB_REF_RE =
  /^(?:kb\/)?((?:topics\/[^\s]+|syntheses\/[^\s]+|\d{4}\/\d{2}\/\d{2}\/[^\s]+))\.md$/;

function refToHref(match: string): string | null {
  const dateMatch = match.match(/^(\d{4})\/(\d{2})\/(\d{2})\/(.+)$/);
  if (dateMatch) {
    const [, y, m, d, rest] = dateMatch;
    if (rest === "README") return `/day/${y}/${m}/${d}`;
    return `/p/${y}/${m}/${d}/${rest}`;
  }
  if (match.startsWith("topics/")) return `/${match}`;
  if (match.startsWith("syntheses/")) return `/${match}`;
  return null;
}

const linkifyKbRefs: Plugin<[], Root> = () => {
  return (tree) => {
    visit(tree, "inlineCode", (node: InlineCode, index, parent) => {
      if (!parent || typeof index !== "number") return;
      // Skip if already inside a link.
      if (parent.type === "link") return;

      const m = node.value.match(KB_REF_RE);
      if (!m) return;
      const href = refToHref(m[1]);
      if (!href) return;

      const link: Link = {
        type: "link",
        url: href,
        title: null,
        children: [{ type: "inlineCode", value: node.value }],
      };
      parent.children.splice(index, 1, link);
      return [SKIP, index + 1];
    });
  };
};

export default linkifyKbRefs;
