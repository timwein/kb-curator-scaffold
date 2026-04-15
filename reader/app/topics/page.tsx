import Link from "next/link";
import { loadManifest } from "@/lib/manifest";
import PageShell from "@/components/layout/PageShell";

export const dynamic = "force-static";

export default async function TopicsIndex() {
  const manifest = await loadManifest();
  const topics = manifest
    .filter((p) => p.kind === "topic")
    .sort((a, b) => a.title.localeCompare(b.title));

  // Count pages per topic.
  const tagCounts = new Map<string, number>();
  for (const p of manifest) {
    for (const t of p.topics) {
      tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
    }
  }

  return (
    <PageShell manifest={manifest}>
      <h1 className="mb-6 text-3xl font-bold">Topics</h1>
      <ul className="divide-y divide-[var(--border)]">
        {topics.map((t) => (
          <li key={t.path} className="py-3">
            <Link
              href={`/topics/${t.slug}`}
              className="flex items-baseline justify-between gap-4 hover:text-[var(--accent)]"
            >
              <span className="font-medium">{t.title}</span>
              <span className="text-sm text-[var(--muted)]">
                {tagCounts.get(t.slug) || 0} tagged
              </span>
            </Link>
            {t.preview && (
              <p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">
                {t.preview.replace(/\n+/g, " ").slice(0, 200)}
              </p>
            )}
          </li>
        ))}
      </ul>
    </PageShell>
  );
}
