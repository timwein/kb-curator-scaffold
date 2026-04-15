"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Fuse from "fuse.js";
import type { ManifestEntry } from "@/lib/kb/types";
import { hrefForEntry } from "@/lib/routing";

export default function SearchPage() {
  const [manifest, setManifest] = useState<ManifestEntry[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    fetch("/pages.json")
      .then((r) => r.json())
      .then((data: ManifestEntry[]) => setManifest(data))
      .catch(() => setManifest([]));
  }, []);

  const fuse = useMemo(
    () =>
      new Fuse(manifest, {
        keys: [
          { name: "title", weight: 0.6 },
          { name: "topics", weight: 0.25 },
          { name: "preview", weight: 0.15 },
        ],
        threshold: 0.35,
        ignoreLocation: true,
      }),
    [manifest],
  );

  const results = useMemo(() => {
    if (!q.trim()) return [];
    return fuse.search(q.trim()).slice(0, 50);
  }, [q, fuse]);

  return (
    <div className="mx-auto w-full max-w-[720px] px-4 py-10 sm:px-8">
      <h1 className="mb-6 text-3xl font-bold">Search</h1>
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus
        placeholder="Search titles, topics, previews…"
        className="w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-4 py-3 text-base outline-none focus:border-[var(--accent)]"
      />
      <p className="mt-2 text-xs text-[var(--muted)]">
        {manifest.length === 0
          ? "Loading index…"
          : `${manifest.length} pages indexed`}
      </p>

      <ul className="mt-6 divide-y divide-[var(--border)]">
        {results.map(({ item }) => {
          const link = hrefForEntry(item);
          return (
            <li key={item.path} className="py-3">
              <Link href={link} className="group block">
                <div className="flex items-baseline justify-between gap-4">
                  <span className="flex-1 font-medium group-hover:text-[var(--accent)]">
                    {item.title}
                  </span>
                  <span className="shrink-0 text-xs uppercase tracking-wide text-[var(--muted)]">
                    {item.kind}
                    {item.date && ` · ${item.date}`}
                  </span>
                </div>
                {item.preview && (
                  <p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">
                    {item.preview.replace(/\n+/g, " ").slice(0, 200)}
                  </p>
                )}
              </Link>
            </li>
          );
        })}
        {q.trim() && results.length === 0 && manifest.length > 0 && (
          <li className="py-6 text-center text-[var(--muted)]">No matches.</li>
        )}
      </ul>
    </div>
  );
}

