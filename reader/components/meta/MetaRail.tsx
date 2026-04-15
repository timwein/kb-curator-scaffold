import Link from "next/link";
import type { KbPage } from "@/lib/kb/types";

export default function MetaRail({
  page,
  children,
}: {
  page: KbPage;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-5 text-sm">
      {page.url && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Source
          </div>
          <a
            href={page.url}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-[var(--accent)] hover:underline"
          >
            {page.url}
          </a>
        </div>
      )}

      {page.byline && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Byline
          </div>
          <div>{page.byline}</div>
        </div>
      )}

      {page.relevanceScore !== null && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Agent relevance
          </div>
          <div>{page.relevanceScore} / 10</div>
        </div>
      )}

      {page.topics.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Topics
          </div>
          <div className="flex flex-wrap gap-1.5">
            {page.topics.map((t) => (
              <Link
                key={t}
                href={`/topics/${t}`}
                className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-xs hover:text-[var(--accent)]"
              >
                {t}
              </Link>
            ))}
          </div>
        </div>
      )}

      {page.slot && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Slot
          </div>
          <div>{page.slot}</div>
        </div>
      )}

      {children}
    </div>
  );
}
