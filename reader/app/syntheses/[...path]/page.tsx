import { notFound } from "next/navigation";
import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";

export const dynamic = "force-static";

export async function generateStaticParams() {
  const manifest = await loadManifest();
  return manifest
    .filter((p) => p.kind === "synthesis" && p.path.startsWith("syntheses/"))
    .map((p) => {
      const rel = p.path.replace(/^syntheses\//, "").replace(/\.md$/, "");
      return { path: rel.split("/") };
    });
}

export default async function SynthesisPage({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path: segs } = await params;
  const manifest = await loadManifest();
  const relPath = `syntheses/${segs.join("/")}.md`;
  const page = await loadPageByRelPath(relPath);
  if (!page) return notFound();

  const content = await renderMdx(page.body, { sourcePath: relPath });

  return (
    <PageShell manifest={manifest}>
      <div className="mb-3 text-sm text-[var(--muted)]">Synthesis</div>
      <h1 className="mb-6 text-3xl font-bold">{page.title}</h1>
      <div className="prose dark:prose-invert max-w-none">{content}</div>
    </PageShell>
  );
}
