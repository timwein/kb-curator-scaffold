import React from "react";
import { compileMDX } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypePrettyCode from "rehype-pretty-code";
import rewriteInternalLinks from "./rewriteInternalLinks";
import linkifyKbRefs from "./linkifyKbRefs";
import externalLinks from "./externalLinks";
import { sanitizeMdx } from "./sanitize";

export interface CompileOpts {
  /** Repo-relative source path, used by the link rewriter. */
  sourcePath: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components?: Record<string, React.ComponentType<any>>;
}

export async function renderMdx(body: string, opts: CompileOpts) {
  const { content } = await compileMDX({
    source: sanitizeMdx(body),
    components: opts.components,
    options: {
      mdxOptions: {
        remarkPlugins: [
          remarkGfm,
          linkifyKbRefs,
          [rewriteInternalLinks, { sourcePath: opts.sourcePath }],
        ],
        rehypePlugins: [
          rehypeSlug,
          [
            rehypeAutolinkHeadings,
            { behavior: "wrap", properties: { className: ["anchor"] } },
          ],
          externalLinks,
          [
            rehypePrettyCode,
            {
              theme: { dark: "github-dark-dimmed", light: "github-light" },
              keepBackground: false,
            },
          ],
        ],
      },
    },
  });
  return content;
}
