import Link from "next/link";
import { notFound } from "next/navigation";
import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";

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

function kindLabel(kind: string): string {
  switch (kind) {
    case "blog":
      return "Blog";
    case "tweet":
      return "Tweet";
    case "synthesis":
      return "Synthesis";
    case "daily":
      return "Daily";
    default:
      return kind;
  }
}

export default async function DayPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { yyyy, mm, dd } = await params;
  const iso = `${yyyy}-${mm}-${dd}`;
  const manifest = await loadManifest();

  const pagesOfDay = manifest
    .filter((p) => p.date === iso && p.kind !== "runlog")
    .sort((a, b) => {
      if (a.kind === "daily") return -1;
      if (b.kind === "daily") return 1;
      return a.path.localeCompare(b.path);
    });

  if (pagesOfDay.length === 0) return notFound();

  const readmePath = `${yyyy}/${mm}/${dd}/README.md`;
  const readme = await loadPageByRelPath(readmePath);
  const readmeContent = readme
    ? await renderMdx(readme.body, { sourcePath: readmePath })
    : null;

  const nice = new Date(iso).toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const nonReadme = pagesOfDay.filter((p) => p.kind !== "daily");

  return (
    <PageShell manifest={manifest}>
      <div className="mb-2 text-sm text-[var(--muted)]">Day view</div>
      <h1 className="mb-6 text-3xl font-bold">{nice}</h1>

      {readmeContent && (
        <div className="prose dark:prose-invert mb-10 max-w-none">
          {readmeContent}
        </div>
      )}

      <h2 className="mb-3 text-xl font-semibold">Pages in this day</h2>
      <ul className="divide-y divide-[var(--border)]">
        {nonReadme.map((p) => (
          <li key={p.path} className="py-3">
            <Link
              href={`/p/${yyyy}/${mm}/${dd}/${p.slug}`}
              className="group flex items-baseline justify-between gap-4"
            >
              <span className="flex-1 group-hover:text-[var(--accent)]">
                {p.title}
              </span>
              <span className="shrink-0 text-xs uppercase tracking-wide text-[var(--muted)]">
                {kindLabel(p.kind)}
                {p.userScore !== null && ` · ${p.userScore}/10`}
              </span>
            </Link>
            {p.topics.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1 text-xs text-[var(--muted)]">
                {p.topics.slice(0, 4).map((t) => (
                  <span key={t}>#{t}</span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </PageShell>
  );
}
