import Link from "next/link";
import { notFound } from "next/navigation";
import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";

export const dynamic = "force-static";

export async function generateStaticParams() {
  const manifest = await loadManifest();
  return manifest
    .filter((p) => p.kind === "topic")
    .map((p) => ({ slug: p.slug }));
}

export default async function TopicPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const manifest = await loadManifest();
  const relPath = `topics/${slug}.md`;
  const page = await loadPageByRelPath(relPath);
  if (!page) return notFound();

  const content = await renderMdx(page.body, { sourcePath: relPath });

  const tagged = manifest
    .filter((p) => p.topics.includes(slug) && p.date !== null)
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));

  return (
    <PageShell manifest={manifest}>
      <div className="mb-3 text-sm text-[var(--muted)]">Topic</div>
      <h1 className="mb-6 text-3xl font-bold">{page.title}</h1>

      {page.body.trim().length > 0 && (
        <div className="prose dark:prose-invert mb-10 max-w-none">
          {content}
        </div>
      )}

      <h2 className="mb-3 text-xl font-semibold">
        Pages tagged <code>{slug}</code>
      </h2>
      {tagged.length === 0 ? (
        <p className="text-[var(--muted)]">No pages tagged yet.</p>
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {tagged.map((p) => {
            const [y, m, d] = (p.date || "").split("-");
            return (
              <li key={p.path} className="py-3">
                <Link
                  href={`/p/${y}/${m}/${d}/${p.slug}`}
                  className="flex items-baseline justify-between gap-4 hover:text-[var(--accent)]"
                >
                  <span className="flex-1">{p.title}</span>
                  <span className="shrink-0 text-xs uppercase tracking-wide text-[var(--muted)]">
                    {p.date}
                    {p.userScore !== null && ` · ${p.userScore}/10`}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </PageShell>
  );
}
