import Link from "next/link";
import { loadManifest } from "@/lib/manifest";
import { hrefForEntry } from "@/lib/routing";
import PageShell from "@/components/layout/PageShell";

export const dynamic = "force-static";

export default async function SynthesesIndex() {
  const manifest = await loadManifest();
  const syntheses = manifest
    .filter((p) => p.kind === "synthesis")
    .sort((a, b) => b.path.localeCompare(a.path));

  return (
    <PageShell manifest={manifest}>
      <h1 className="mb-6 text-3xl font-bold">Syntheses</h1>
      {syntheses.length === 0 ? (
        <p className="text-[var(--muted)]">No syntheses yet.</p>
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {syntheses.map((s) => (
            <li key={s.path} className="py-3">
              <Link
                href={hrefForEntry(s)}
                className="flex items-baseline justify-between gap-4 hover:text-[var(--accent)]"
              >
                <span className="flex-1">{s.title}</span>
                <span className="shrink-0 text-xs text-[var(--muted)]">
                  {s.date || s.path.split("/").slice(0, -1).join("/")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}
