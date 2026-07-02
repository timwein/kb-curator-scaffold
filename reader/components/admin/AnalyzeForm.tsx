"use client";

import { useState } from "react";

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; runUrl: string; url: string; fallback: boolean }
  | { kind: "error"; message: string };

export default function AnalyzeForm() {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setState({ kind: "error", message: "URL is required" });
      return;
    }

    setState({ kind: "submitting" });
    try {
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: trimmed }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        error?: string;
        runUrl?: string;
        url?: string;
        fallback?: boolean;
      };
      if (!res.ok || !body.ok) {
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      setState({
        kind: "success",
        runUrl: body.runUrl || "",
        url: body.url || trimmed,
        fallback: Boolean(body.fallback),
      });
      setUrl("");
    } catch (err) {
      setState({ kind: "error", message: (err as Error).message });
    }
  }

  const submitting = state.kind === "submitting";

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <label
          htmlFor="analyze-url"
          className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]"
        >
          URL to analyze
        </label>
        <input
          id="analyze-url"
          type="url"
          required
          inputMode="url"
          autoComplete="off"
          placeholder="https://example.com/some-blog-post"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={submitting}
          className="w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={submitting || !url.trim()}
          className="self-start rounded-md border border-[var(--accent)] bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Queueing…" : "Queue analysis"}
        </button>
      </form>

      {state.kind === "success" && (
        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
          <div className="mb-2 font-semibold text-green-700 dark:text-green-400">
            Analysis queued
          </div>
          <p className="mb-2 text-[var(--muted)]">
            The agent is analyzing{" "}
            <code className="break-all text-[var(--foreground)]">
              {state.url}
            </code>
            . It typically takes 2–5 minutes. When it finishes, the new page
            will appear under today&rsquo;s date in the reader.
          </p>
          {state.runUrl && (
            <a
              href={state.runUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block text-[var(--accent)] underline hover:opacity-80"
            >
              {state.fallback ? "Open Actions tab" : "View GitHub Actions run"}{" "}
              ↗
            </a>
          )}
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          <div className="mb-1 font-semibold">Could not queue analysis</div>
          <div>{state.message}</div>
        </div>
      )}
    </div>
  );
}
