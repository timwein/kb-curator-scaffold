import Link from "next/link";
import type { ManifestEntry } from "@/lib/kb/types";

interface DayTreeProps {
  manifest: ManifestEntry[];
}

interface DayNode {
  y: string;
  m: string;
  d: string;
  iso: string;
  pageCount: number;
}

export default function DayTree({ manifest }: DayTreeProps) {
  // Collect unique day folders from dated pages (not runlogs).
  const dayMap = new Map<string, DayNode>();
  for (const p of manifest) {
    if (!p.date || p.kind === "runlog") continue;
    const [y, m, d] = p.date.split("-");
    const key = `${y}-${m}-${d}`;
    const existing = dayMap.get(key);
    if (existing) {
      existing.pageCount++;
    } else {
      dayMap.set(key, { y, m, d, iso: p.date, pageCount: 1 });
    }
  }
  const days = Array.from(dayMap.values()).sort((a, b) =>
    b.iso.localeCompare(a.iso),
  );

  // Group by year → month → days
  const byYear = new Map<string, Map<string, DayNode[]>>();
  for (const day of days) {
    if (!byYear.has(day.y)) byYear.set(day.y, new Map());
    const months = byYear.get(day.y)!;
    if (!months.has(day.m)) months.set(day.m, []);
    months.get(day.m)!.push(day);
  }
  const years = Array.from(byYear.entries()).sort((a, b) =>
    b[0].localeCompare(a[0]),
  );

  if (days.length === 0) {
    return <p className="text-xs text-[var(--muted)]">No dated content yet.</p>;
  }

  const latestYear = years[0][0];

  return (
    <nav className="text-sm">
      {years.map(([year, months]) => {
        const monthList = Array.from(months.entries()).sort((a, b) =>
          b[0].localeCompare(a[0]),
        );
        return (
          <details key={year} open={year === latestYear} className="mb-1">
            <summary className="cursor-pointer py-1 font-medium">
              {year}
            </summary>
            <div className="ml-3">
              {monthList.map(([month, dayList]) => {
                const monthName = new Date(
                  Number(year),
                  Number(month) - 1,
                  1,
                ).toLocaleString("en-US", { month: "short" });
                return (
                  <details
                    key={`${year}-${month}`}
                    open={year === latestYear && month === monthList[0][0]}
                    className="mb-1"
                  >
                    <summary className="cursor-pointer py-0.5 text-[var(--muted)]">
                      {monthName}
                    </summary>
                    <ul className="ml-3 border-l border-[var(--border)] pl-2">
                      {dayList.map((day) => (
                        <li key={day.iso} className="py-0.5">
                          <Link
                            href={`/day/${day.y}/${day.m}/${day.d}`}
                            className="hover:text-[var(--accent)]"
                          >
                            {day.d}
                            <span className="ml-1 text-xs text-[var(--muted)]">
                              · {day.pageCount}
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </details>
                );
              })}
            </div>
          </details>
        );
      })}
    </nav>
  );
}
