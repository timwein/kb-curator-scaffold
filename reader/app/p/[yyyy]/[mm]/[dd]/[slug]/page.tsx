import { notFound } from "next/navigation";
import path from "node:path";
import fs from "node:fs/promises";
import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath, resolveKbRoot } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";
import MetaRail from "@/components/meta/MetaRail";
import InteractivePanel from "@/components/interactive/InteractivePanel";

export const dynamic = "force-static";

interface Params {
  yyyy: string;
  mm: string;
  dd: string;
  slug: string;
}

export async function generateStaticParams() {
  const manifest = await loadManifest();
  const params: Params[] = [];
  for (const p of manifest) {
    if (!p.date || p.kind === "runlog" || p.kind === "daily") continue;
    const [yyyy, mm, dd] = p.date.split("-");
    params.push({ yyyy, mm, dd, slug: p.slug });
  }
  return params;
}

/**
 * Find the page file even when slug collides across folders — we match by
 * `date + slug`. Tests against both .md and any case drift.
 */
async function findPageFile(
  yyyy: string,
  mm: string,
  dd: string,
  slug: string,
): Promise<string | null> {
  const root = resolveKbRoot();
  const dir = path.join(root, yyyy, mm, dd);
  let entries: string[];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return null;
  }
  const match = entries.find((name) => name === `${slug}.md`);
  if (!match) return null;
  return `${yyyy}/${mm}/${dd}/${match}`;
}

export default async function PageView({
  params,
}: {
  params: Promise<Params>;
}) {
  const { yyyy, mm, dd, slug } = await params;
  const manifest = await loadManifest();
  const relPath = await findPageFile(yyyy, mm, dd, slug);
  if (!relPath) return notFound();
  const page = await loadPageByRelPath(relPath);
  if (!page) return notFound();
  if (page.kind === "runlog") return notFound();

  const content = await renderMdx(page.body, { sourcePath: page.path });

  return (
    <PageShell
      manifest={manifest}
      rail={<MetaRail page={page} />}
    >
      <div className="mb-3 text-sm text-[var(--muted)]">
        {page.date}
        {page.kind !== "other" &&
          ` · ${page.kind.charAt(0).toUpperCase() + page.kind.slice(1)}`}
      </div>
      <h1 className="mb-1 text-3xl font-bold leading-tight">
        {page.url ? (
          <a
            href={page.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-[var(--accent)]"
          >
            {page.title}
          </a>
        ) : (
          page.title
        )}
      </h1>
      {page.byline && (
        <div className="mb-6 text-sm italic text-[var(--muted)]">
          {page.byline}
        </div>
      )}

      <div className="prose dark:prose-invert max-w-none">{content}</div>

      {page.canRate && (
        <InteractivePanel
          pagePath={page.path}
          initialScore={page.userScore}
          pageTitle={page.title}
        />
      )}
    </PageShell>
  );
}
