import Link from "next/link";
import { loadManifest } from "@/lib/manifest";
import PageShell from "@/components/layout/PageShell";

export const dynamic = "force-static";

export default async function DaysPage() {
  const manifest = await loadManifest();

  const dayMap = new Map<string, number>();
  for (const p of manifest) {
    if (!p.date || p.kind === "runlog") continue;
    dayMap.set(p.date, (dayMap.get(p.date) || 0) + 1);
  }
  const days = Array.from(dayMap.entries()).sort((a, b) =>
    b[0].localeCompare(a[0]),
  );

  return (
    <PageShell manifest={manifest}>
      <h1 className="mb-6 text-3xl font-bold">All days</h1>
      <ul className="divide-y divide-[var(--border)]">
        {days.map(([iso, count]) => {
          const [y, m, d] = iso.split("-");
          const nice = new Date(iso).toLocaleDateString("en-US", {
            weekday: "short",
            year: "numeric",
            month: "short",
            day: "numeric",
          });
          return (
            <li key={iso} className="py-3">
              <Link
                href={`/day/${y}/${m}/${d}`}
                className="flex items-baseline justify-between gap-4 hover:text-[var(--accent)]"
              >
                <span>{nice}</span>
                <span className="text-sm text-[var(--muted)]">
                  {count} {count === 1 ? "page" : "pages"}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </PageShell>
  );
}
