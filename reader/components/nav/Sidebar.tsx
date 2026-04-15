import Link from "next/link";
import type { ManifestEntry } from "@/lib/kb/types";
import { hrefForEntry } from "@/lib/routing";
import DayTree from "./DayTree";

interface SidebarProps {
  manifest: ManifestEntry[];
}

export default function Sidebar({ manifest }: SidebarProps) {
  const topics = manifest
    .filter((p) => p.kind === "topic")
    .sort((a, b) => a.title.localeCompare(b.title));
  const syntheses = manifest
    .filter((p) => p.kind === "synthesis")
    .sort((a, b) => b.path.localeCompare(a.path));

  return (
    <aside className="w-64 shrink-0 border-r border-[var(--border)] px-4 py-6 text-sm">
      <div className="mb-4">
        <Link
          href="/"
          className="text-base font-semibold hover:text-[var(--accent)]"
        >
          KB Reader
        </Link>
      </div>

      <nav className="mb-6 flex flex-col gap-1">
        <Link href="/" className="hover:text-[var(--accent)]">
          Latest
        </Link>
        <Link href="/days" className="hover:text-[var(--accent)]">
          All days
        </Link>
        <Link href="/topics" className="hover:text-[var(--accent)]">
          Topics
        </Link>
        <Link href="/syntheses" className="hover:text-[var(--accent)]">
          Syntheses
        </Link>
        <Link href="/search" className="hover:text-[var(--accent)]">
          Search
        </Link>
      </nav>

      <section className="mb-6">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Days
        </h3>
        <DayTree manifest={manifest} />
      </section>

      {topics.length > 0 && (
        <section className="mb-6">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Topics
          </h3>
          <ul className="flex flex-col gap-0.5">
            {topics.map((t) => (
              <li key={t.path}>
                <Link
                  href={`/topics/${t.slug}`}
                  className="hover:text-[var(--accent)]"
                >
                  {t.title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {syntheses.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Recent Syntheses
          </h3>
          <ul className="flex flex-col gap-0.5">
            {syntheses.slice(0, 8).map((s) => (
              <li key={s.path}>
                <Link
                  href={hrefForEntry(s)}
                  className="block truncate hover:text-[var(--accent)]"
                  title={s.title}
                >
                  {s.title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
