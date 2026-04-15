"use client";

import { useState } from "react";
import type { ManifestEntry } from "@/lib/kb/types";
import Sidebar from "./Sidebar";

export default function MobileDrawer({
  manifest,
}: {
  manifest: ManifestEntry[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open navigation"
        className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm lg:hidden"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
        >
          <path
            d="M2 4h12M2 8h12M2 12h12"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
        Menu
      </button>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="relative flex h-full w-72 max-w-[85%] flex-col bg-[var(--background)] shadow-xl">
            <div className="flex justify-end p-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close navigation"
                className="rounded-md p-2 hover:bg-[var(--surface)]"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto" onClick={() => setOpen(false)}>
              <Sidebar manifest={manifest} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
