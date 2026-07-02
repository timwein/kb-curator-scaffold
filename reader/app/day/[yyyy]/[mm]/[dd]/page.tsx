import { notFound } from "next/navigation";
import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";
import ReadableLink from "@/components/day/ReadableLink";
import { buildSyntheticReadme } from "@/lib/kb/syntheticReadme";

export const dynamic = "force-static";

interface Params {
  yyyy: string;
  mm: string;
  dd: string;
}

export async function generateStaticParams() {
  const manifest = await loadManifest();
  const seen = new Set<string>();
  const out: Params[] = [];
  for (const p of manifest) {
    if (!p.date) continue;
    const [yyyy, mm, dd] = p.date.split("-");
    const k = `${yyyy}/${mm}/${dd}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ yyyy, mm, dd });
  }
  return out;
}

export default async function DayPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { yyyy, mm, dd } = await params;
  const iso = `${yyyy}-${mm}-${dd}`;
  const manifest = await loadManifest();

  const allPagesOfDay = manifest
    .filter((p) => p.date === iso)
    .sort((a, b) => {
      if (a.kind === "daily") return -1;
      if (b.kind === "daily") return 1;
      return a.path.localeCompare(b.path);
    });

  if (allPagesOfDay.length === 0) return notFound();

  const readmePath = `${yyyy}/${mm}/${dd}/README.md`;
  const readme = await loadPageByRelPath(readmePath);

  const body = readme ? readme.body : buildSyntheticReadme(allPagesOfDay);
  const content = body
    ? await renderMdx(body, {
        sourcePath: readmePath,
        components: { a: ReadableLink },
      })
    : null;

  const nice = new Date(iso).toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return (
    <PageShell manifest={manifest}>
      <div className="mb-2 text-sm text-[var(--muted)]">Day view</div>
      <h1 className="mb-6 text-3xl font-bold">{nice}</h1>

      {content && (
        <div className="prose dark:prose-invert max-w-none">{content}</div>
      )}
    </PageShell>
  );
}
