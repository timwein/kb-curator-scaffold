import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

/**
 * Quartz 4 configuration for the user,'s AI Research Knowledge Base
 * GitHub repo: <your-username>/<your-kb-repo>
 * Deployed to: Cloudflare Pages (ai-research-kb.pages.dev)
 */
const config: QuartzConfig = {
  configuration: {
    pageTitle: "AI Research KB",
    pageTitleSuffix: " — your Knowledge Base",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: "ai-research-kb.pages.dev",
    ignorePatterns: [
      "private",
      "templates",
      ".obsidian",
      "*.py",
      "*.yml",
      "*.yaml",
      "*.json",
      "*.jsonl",
      "*.sh",
      "*.docx",
      "node_modules",
      ".github",
      "quartz",
      "blogs-ingested.jsonl",
      "tweets-ingested.jsonl",
    ],
    defaultDateType: "modified",
    theme: {
      cdnCaching: true,
      typography: {
        header: "Inter",
        body: "Inter",
        code: "JetBrains Mono",
      },
      colors: {
        lightMode: {
          light: "#fafafa",
          lightgray: "#e5e5e5",
          gray: "#a3a3a3",
          darkgray: "#404040",
          dark: "#171717",
          secondary: "#7c3aed",   // violet-600 — primary accent
          tertiary: "#a78bfa",    // violet-400 — hover/link
          highlight: "rgba(124, 58, 237, 0.08)",
          textHighlight: "rgba(124, 58, 237, 0.15)",
        },
        darkMode: {
          light: "#18181b",
          lightgray: "#27272a",
          gray: "#71717a",
          darkgray: "#d4d4d8",
          dark: "#fafafa",
          secondary: "#a78bfa",   // violet-400
          tertiary: "#c4b5fd",    // violet-300
          highlight: "rgba(167, 139, 250, 0.10)",
          textHighlight: "rgba(167, 139, 250, 0.18)",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      Plugin.SyntaxHighlighting(),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents({ maxDepth: 3 }),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description({ descriptionLength: 200 }),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
        rssFullHtml: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config
