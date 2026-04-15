import { compileMDX } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypePrettyCode from "rehype-pretty-code";
import rewriteInternalLinks from "./rewriteInternalLinks";
import externalLinks from "./externalLinks";
import { sanitizeMdx } from "./sanitize";

export interface CompileOpts {
  /** Repo-relative source path, used by the link rewriter. */
  sourcePath: string;
}

export async function renderMdx(body: string, opts: CompileOpts) {
  const { content } = await compileMDX({
    source: sanitizeMdx(body),
    options: {
      mdxOptions: {
        remarkPlugins: [
          remarkGfm,
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
