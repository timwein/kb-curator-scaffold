"use client";

import Link from "next/link";
import { useReadPages } from "@/hooks/useReadPages";

interface Props extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  href?: string;
}

function hrefToRelPath(href: string): string | null {
  if (!href.startsWith("/p/")) return null;
  const rest = href.slice("/p/".length);
  if (!/^\d{4}\/\d{2}\/\d{2}\/.+$/.test(rest)) return null;
  return `${rest}.md`;
}

export default function ReadableLink({ href, children, ...rest }: Props) {
  const relPath = typeof href === "string" ? hrefToRelPath(href) : null;
  const { read, toggle } = useReadPages();

  if (!relPath || typeof href !== "string") {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  }

  const isRead = read.has(relPath);

  function handleToggle(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    void toggle(relPath!, !isRead);
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
      <button
        onClick={handleToggle}
        aria-label={isRead ? "Mark as unread" : "Mark as read"}
        style={{
          flexShrink: 0,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: "14px",
          height: "14px",
          minWidth: "14px",
          borderRadius: "3px",
          border: `1px solid ${isRead ? "var(--accent)" : "var(--muted)"}`,
          backgroundColor: isRead ? "var(--accent)" : "transparent",
          cursor: "pointer",
          padding: 0,
          verticalAlign: "middle",
        }}
      >
        {isRead && (
          <svg width="8" height="7" viewBox="0 0 8 7" fill="none">
            <path
              d="M1 3.5L3 5.5L7 1"
              stroke="white"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
      <Link href={href} {...(rest as object)} style={{ opacity: isRead ? 0.5 : 1 }}>
        {children}
      </Link>
    </span>
  );
}
