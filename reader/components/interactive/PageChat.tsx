"use client";

import { useRef, useState } from "react";

type Role = "user" | "assistant";
interface Message {
  role: Role;
  content: string;
}

const MODELS = [
  { id: "claude-opus-4-6", label: "Opus 4.6" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6" },
] as const;
type ModelId = (typeof MODELS)[number]["id"];

export default function PageChat({
  pagePath,
  pageTitle,
}: {
  pagePath: string;
  pageTitle: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState<ModelId>("claude-opus-4-6");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function send() {
    const trimmed = input.trim();
    if (!trimmed || streaming) return;
    const next: Message[] = [
      ...messages,
      { role: "user", content: trimmed },
      { role: "assistant", content: "" },
    ];
    setMessages(next);
    setInput("");
    setStreaming(true);
    setError(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          pagePath,
          model,
          messages: next
            .slice(0, -1) // drop empty trailing assistant placeholder
            .map((m) => ({ role: m.role, content: m.content })),
        }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE frames: `data: {json}\n\n`
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const evt = JSON.parse(payload) as
              | { type: "delta"; text: string }
              | { type: "error"; message: string };
            if (evt.type === "delta") {
              setMessages((prev) => {
                const copy = prev.slice();
                const last = copy[copy.length - 1];
                copy[copy.length - 1] = {
                  ...last,
                  content: last.content + evt.text,
                };
                return copy;
              });
            } else if (evt.type === "error") {
              setError(evt.message);
            }
          } catch {
            // ignore malformed frame
          }
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name !== "AbortError") {
        setError((err as Error).message);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  return (
    <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Ask Claude about this page
        </h3>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value as ModelId)}
          disabled={streaming}
          className="rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-xs"
        >
          {MODELS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      {messages.length === 0 && !streaming && (
        <p className="mb-3 text-xs text-[var(--muted)]">
          The full text of &ldquo;{pageTitle}&rdquo; is prepended as context.
          Ask follow-ups, request clarification, or pressure-test the
          argument. New conversation per page load — nothing is saved.
        </p>
      )}

      {messages.length > 0 && (
        <div className="mb-3 space-y-3 max-h-96 overflow-y-auto">
          {messages.map((m, i) => (
            <div
              key={i}
              className={`rounded-md border px-3 py-2 text-sm ${
                m.role === "user"
                  ? "border-[var(--border)] bg-[var(--background)]"
                  : "border-[var(--border)] bg-[var(--surface)]"
              }`}
            >
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                {m.role === "user" ? "You" : "Claude"}
              </div>
              <div className="whitespace-pre-wrap leading-relaxed">
                {m.content || (streaming && i === messages.length - 1 ? "…" : "")}
              </div>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="mb-2 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}

      <div className="flex items-end gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
          disabled={streaming}
          rows={2}
          placeholder="Ask a follow-up… (⌘/Ctrl + Enter)"
          className="min-h-[2.5rem] flex-1 resize-y rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
        />
        {streaming ? (
          <button
            type="button"
            onClick={stop}
            className="h-10 rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={send}
            disabled={!input.trim()}
            className="h-10 rounded-md bg-[var(--accent)] px-4 text-sm font-medium text-white disabled:opacity-40"
          >
            Send
          </button>
        )}
      </div>
    </section>
  );
}
