import { loadManifest } from "@/lib/manifest";
import { loadPageByRelPath } from "@/lib/kb/scan";
import { renderMdx } from "@/lib/mdx/compile";
import PageShell from "@/components/layout/PageShell";
import Link from "next/link";
import ReadableLink from "@/components/day/ReadableLink";
import { buildSyntheticReadme } from "@/lib/kb/syntheticReadme";

export const dynamic = "force-static";

export default async function HomePage() {
  const manifest = await loadManifest();

  const latestDate = manifest
    .filter((p) => p.date !== null && p.kind !== "runlog")
    .map((p) => p.date as string)
    .sort((a, b) => b.localeCompare(a))[0];

  if (!latestDate) {
    return (
      <PageShell manifest={manifest}>
        <h1 className="mb-4 text-3xl font-bold">KB Reader</h1>
        <p className="text-[var(--muted)]">
          No content scanned yet. Run the build to generate the manifest.
        </p>
      </PageShell>
    );
  }

  const [y, m, d] = latestDate.split("-");
  const nice = new Date(latestDate).toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const dailyForLatest = manifest.find(
    (p) => p.kind === "daily" && p.date === latestDate,
  );
  const dailyPage = dailyForLatest
    ? await loadPageByRelPath(dailyForLatest.path)
    : null;

  if (dailyPage) {
    const content = await renderMdx(dailyPage.body, {
      sourcePath: dailyPage.path,
      components: { a: ReadableLink },
    });
    return (
      <PageShell manifest={manifest}>
        <div className="mb-3 text-sm text-[var(--muted)]">
          Latest day · {dailyPage.date}
        </div>
        <h1 className="mb-2 text-3xl font-bold">{dailyPage.title}</h1>
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

  const pagesOfDay = manifest
    .filter((p) => p.date === latestDate)
    .sort((a, b) => {
      if (a.kind === "daily") return -1;
      if (b.kind === "daily") return 1;
      return a.path.localeCompare(b.path);
    });

  const syntheticBody = buildSyntheticReadme(pagesOfDay);
  const content = syntheticBody
    ? await renderMdx(syntheticBody, {
        sourcePath: `${y}/${m}/${d}/README.md`,
        components: { a: ReadableLink },
      })
    : null;

  return (
    <PageShell manifest={manifest}>
      <div className="mb-3 text-sm text-[var(--muted)]">
        Latest day · {latestDate}
      </div>
      <h1 className="mb-2 text-3xl font-bold">{nice}</h1>
      <div className="mt-6 flex items-center gap-3 text-sm">
        <Link
          href={`/day/${y}/${m}/${d}`}
          className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 hover:text-[var(--accent)]"
        >
          Open day view
        </Link>
      </div>
      <hr className="my-6 border-[var(--border)]" />
      {content && (
        <div className="prose dark:prose-invert max-w-none">{content}</div>
      )}
    </PageShell>
  );
}
