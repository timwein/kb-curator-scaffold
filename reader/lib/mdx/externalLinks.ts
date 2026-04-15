import type { Plugin } from "unified";
import type { Root, Element } from "hast";
import { visit } from "unist-util-visit";

/**
 * Rehype plugin that marks external http(s) anchor tags with target=_blank
 * and rel=noopener noreferrer. Internal links (already rewritten by the
 * remark plugin) are left alone.
 */
const externalLinks: Plugin<[], Root> = () => {
  return (tree) => {
    visit(tree, "element", (node: Element) => {
      if (node.tagName !== "a") return;
      const href = node.properties?.href;
      if (typeof href !== "string") return;
      if (!/^https?:\/\//i.test(href)) return;
      node.properties = {
        ...node.properties,
        target: "_blank",
        rel: "noopener noreferrer",
      };
    });
  };
};

export default externalLinks;
