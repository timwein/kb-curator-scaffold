"use client";

import Link from "next/link";
import { useReadPages } from "@/hooks/useReadPages";

interface PageEntry {
  path: string;
  slug: string;
  title: string;
  kind: string;
  userScore: number | null;
  topics: string[];
}

interface Props {
  pages: PageEntry[];
  yyyy: string;
  mm: string;
  dd: string;
}

function kindLabel(kind: string): string {
  switch (kind) {
    case "blog": return "Blog";
    case "tweet": return "Tweet";
    case "synthesis": return "Synthesis";
    case "daily": return "Daily";
    default: return kind;
  }
}

export default function DayPageLinks({ pages, yyyy, mm, dd }: Props) {
  const { read, toggle } = useReadPages();

  function handleToggle(path: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    void toggle(path, !read.has(path));
  }

  return (
    <ul className="divide-y divide-[var(--border)]">
      {pages.map((p) => {
        const isRead = read.has(p.path);
        return (
          <li key={p.path} className="py-3">
            <div className="flex items-start gap-3">
              <button
                onClick={(e) => handleToggle(p.path, e)}
                aria-label={isRead ? "Mark as unread" : "Mark as read"}
                className="mt-0.5 shrink-0 flex items-center justify-center w-4 h-4 rounded focus:outline-none focus-visible:ring-2"
                style={{
                  border: `1px solid ${isRead ? "var(--accent)" : "var(--muted)"}`,
                  backgroundColor: isRead ? "var(--accent)" : "transparent",
                }}
              >
                {isRead && (
                  <svg
                    width="10"
                    height="8"
                    viewBox="0 0 10 8"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M1 4L3.5 6.5L9 1"
                      stroke="white"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </button>
              <div className="flex-1 min-w-0">
                <Link
                  href={`/p/${yyyy}/${mm}/${dd}/${p.slug}`}
                  className="group flex items-baseline justify-between gap-4"
                >
                  <span
                    className="flex-1 group-hover:text-[var(--accent)] transition-opacity"
                    style={{ opacity: isRead ? 0.5 : 1 }}
                  >
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
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
