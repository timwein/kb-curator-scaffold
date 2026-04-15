import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

/**
 * Layout configuration for the user,'s AI Research Knowledge Base
 *
 * Left sidebar: navigation, search, theme toggle
 * Right sidebar: graph view, table of contents, backlinks
 * Content area: breadcrumbs, title, metadata, tags, then content
 */

// Components shared across all pages
export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer({
    links: {
      "KB Source": "https://github.com/<your-username>/<your-kb-repo>",
    },
  }),
}

// Layout for individual content pages (analyses, syntheses, topic files)
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ConditionalRender({
      component: Component.Breadcrumbs(),
      condition: (page) => page.fileData.slug !== "index",
    }),
    Component.ArticleTitle(),
    Component.ContentMeta(),
    Component.TagList(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
        { Component: Component.ReaderMode() },
      ],
    }),
    Component.Explorer({
      title: "Knowledge Base",
      folderClickBehavior: "link",
      folderDefaultState: "collapsed",
      useSavedState: true,
    }),
  ],
  right: [
    Component.Graph({
      localGraph: {
        depth: 2,
        linkDistance: 30,
      },
      globalGraph: {
        depth: -1,
        linkDistance: 30,
      },
    }),
    Component.DesktopOnly(Component.TableOfContents()),
    Component.Backlinks(),
  ],
}

// Layout for folder/tag listing pages
export const defaultListPageLayout: PageLayout = {
  beforeBody: [
    Component.Breadcrumbs(),
    Component.ArticleTitle(),
    Component.ContentMeta(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
      ],
    }),
    Component.Explorer({
      title: "Knowledge Base",
      folderClickBehavior: "link",
      folderDefaultState: "collapsed",
      useSavedState: true,
    }),
  ],
  right: [],
}
