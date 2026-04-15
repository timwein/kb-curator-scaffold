"use client";

import { useState } from "react";

interface RatingWidgetProps {
  pagePath: string;
  initialScore: number | null;
}

const SCORES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10] as const;

type SaveState =
  | { kind: "idle" }
  | { kind: "saving"; score: number }
  | { kind: "saved"; noop: boolean }
  | { kind: "error"; message: string };

export default function RatingWidget({
  pagePath,
  initialScore,
}: RatingWidgetProps) {
  const [score, setScore] = useState<number | null>(initialScore);
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  async function save(next: number) {
    const prev = score;
    setScore(next);
    setState({ kind: "saving", score: next });
    try {
      const res = await fetch("/api/rate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: pagePath, score: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      const body = (await res.json()) as { ok: true; noop?: boolean };
      setState({ kind: "saved", noop: Boolean(body.noop) });
      setTimeout(() => {
        setState((s) => (s.kind === "saved" ? { kind: "idle" } : s));
      }, 2500);
    } catch (err) {
      setScore(prev);
      setState({ kind: "error", message: (err as Error).message });
    }
  }

  const disabled = state.kind === "saving";

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Your relevance score
        </div>
        <div className="text-xs text-[var(--muted)]">
          {state.kind === "saving" && "Saving…"}
          {state.kind === "saved" &&
            (state.noop ? "No change" : "Saved to KB")}
          {state.kind === "error" && (
            <span className="text-red-600">Error: {state.message}</span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        {SCORES.map((s) => {
          const selected = score === s;
          return (
            <button
              key={s}
              type="button"
              disabled={disabled}
              onClick={() => save(s)}
              aria-pressed={selected}
              className={`h-8 w-8 rounded-md border text-sm transition ${
                selected
                  ? "border-[var(--accent)] bg-[var(--accent)] text-white"
                  : "border-[var(--border)] bg-[var(--background)] hover:border-[var(--accent)]"
              } disabled:opacity-50`}
            >
              {s}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-[var(--muted)]">
        Saved as <code>user_score</code> in the page&apos;s frontmatter. The
        agents read this on their next run.
      </p>
    </div>
  );
}
