import type { Plugin } from "unified";
import type { Root } from "mdast";
import { visit } from "unist-util-visit";
import path from "node:path";

export interface RewriteOptions {
  /**
   * Repo-relative path of the source file being compiled, e.g.
   * "2026/04/14/README.md" or "syntheses/2026/14-midday.md". Used to resolve
   * sibling-relative and `../`-relative links correctly.
   */
  sourcePath: string;
}

/**
 * Remark plugin that rewrites markdown links pointing at .md files inside the
 * KB into the reader's routes:
 *
 *   2026/04/14/blog-foo.md        -> /p/2026/04/14/blog-foo
 *   ../2026/03/31/foo.md          -> /p/2026/03/31/foo (resolved against sourcePath)
 *   blog-foo.md                   -> /p/<date>/blog-foo (resolved against sourcePath)
 *   topics/foo.md                 -> /topics/foo
 *   syntheses/2026/14-midday.md   -> /syntheses/2026/14-midday
 *
 * External (http/https) links and anchors (#foo) are left alone.
 */
const rewriteInternalLinks: Plugin<[RewriteOptions], Root> = (opts) => {
  const sourceDir = path.posix.dirname(opts.sourcePath);
  return (tree) => {
    visit(tree, "link", (node) => {
      const href = node.url;
      if (!href) return;
      if (/^https?:\/\//i.test(href)) return;
      if (href.startsWith("#")) return;
      if (href.startsWith("mailto:")) return;

      // Drop query/hash for path resolution, re-append at end.
      const hashIdx = href.search(/[?#]/);
      const cleanHref = hashIdx >= 0 ? href.slice(0, hashIdx) : href;
      const suffix = hashIdx >= 0 ? href.slice(hashIdx) : "";

      if (!cleanHref.endsWith(".md")) return;

      // Resolve against the source directory to get an absolute-repo-relative path.
      const resolved = path.posix.normalize(
        cleanHref.startsWith("/")
          ? cleanHref.slice(1)
          : path.posix.join(sourceDir, cleanHref),
      );
      const withoutExt = resolved.replace(/\.md$/, "");

      if (withoutExt.startsWith("topics/")) {
        node.url = "/" + withoutExt + suffix;
        return;
      }
      if (withoutExt.startsWith("syntheses/")) {
        node.url = "/" + withoutExt + suffix;
        return;
      }
      // Date-first: YYYY/MM/DD/slug
      const dateMatch = withoutExt.match(/^(\d{4})\/(\d{2})\/(\d{2})\/(.+)$/);
      if (dateMatch) {
        const [, y, m, d, rest] = dateMatch;
        // If rest is "README", route to /day/...
        if (rest === "README") {
          node.url = `/day/${y}/${m}/${d}${suffix}`;
          return;
        }
        node.url = `/p/${y}/${m}/${d}/${rest}${suffix}`;
        return;
      }
      // Leave anything else untouched (odd paths we don't know how to route).
    });
  };
};

export default rewriteInternalLinks;
