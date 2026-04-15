import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";
import Link from "next/link";

export const dynamic = "force-static";

export default async function HomePage() {
  const manifest = await loadManifest();
  const latestDaily = manifest.find(
    (p) => p.kind === "daily" && p.date !== null,
  );

  if (!latestDaily) {
    return (
      <PageShell manifest={manifest}>
        <h1 className="mb-4 text-3xl font-bold">KB Reader</h1>
        <p className="text-[var(--muted)]">
          No content scanned yet. Run the build to generate the manifest.
        </p>
      </PageShell>
    );
  }

  const page = await loadPageByRelPath(latestDaily.path);
  if (!page) {
    return (
      <PageShell manifest={manifest}>
        <p>Could not load {latestDaily.path}</p>
      </PageShell>
    );
  }

  const content = await renderMdx(page.body, { sourcePath: page.path });
  const [y, m, d] = (page.date || "").split("-");

  return (
    <PageShell manifest={manifest}>
      <div className="mb-3 text-sm text-[var(--muted)]">
        Latest day · {page.date}
      </div>
      <h1 className="mb-2 text-3xl font-bold">{page.title}</h1>
      <div className="mt-6 flex items-center gap-3 text-sm">
        <Link
          href={`/day/${y}/${m}/${d}`}
          className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 hover:text-[var(--accent)]"
        >
          Open day view
        </Link>
      </div>
      <hr className="my-6 border-[var(--border)]" />
      <div className="prose dark:prose-invert max-w-none">{content}</div>
    </PageShell>
  );
}
