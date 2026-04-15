import type { ReactNode } from "react";
import Link from "next/link";
import type { ManifestEntry } from "@/lib/kb/types";
import Sidebar from "../nav/Sidebar";
import MobileDrawer from "../nav/MobileDrawer";

interface PageShellProps {
  manifest: ManifestEntry[];
  children: ReactNode;
  rail?: ReactNode;
}

export default function PageShell({ manifest, children, rail }: PageShellProps) {
  return (
    <div className="flex min-h-screen">
      <div className="hidden lg:block">
        <Sidebar manifest={manifest} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3 lg:hidden">
          <MobileDrawer manifest={manifest} />
          <Link href="/" className="font-semibold">
            KB Reader
          </Link>
          <Link href="/search" className="text-sm text-[var(--muted)]">
            Search
          </Link>
        </header>

        <main className="flex flex-1 flex-col lg:flex-row">
          <article className="min-w-0 flex-1 px-4 py-8 sm:px-8 lg:px-12">
            <div className="mx-auto w-full max-w-[720px]">{children}</div>
          </article>
          {rail && (
            <aside className="hidden w-72 shrink-0 border-l border-[var(--border)] px-5 py-8 xl:block">
              <div className="sticky top-8">{rail}</div>
            </aside>
          )}
        </main>
      </div>
    </div>
  );
}
