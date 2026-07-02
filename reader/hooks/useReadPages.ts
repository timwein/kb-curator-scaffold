"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "reader:read-pages:v2";

function loadCache(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr)
      ? new Set(arr.filter((x): x is string => typeof x === "string"))
      : new Set();
  } catch {
    return new Set();
  }
}

function saveCache(set: Set<string>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]));
  } catch {
    // Storage may be full or disabled; ignore.
  }
}

export function useReadPages() {
  // Start from empty during SSR so hydration matches; hydrate from localStorage
  // in the first client effect.
  const [read, setRead] = useState<Set<string>>(() => new Set());
  // Tracks paths the user toggled this session so an in-flight GET can't
  // overwrite the user's intent with a stale server snapshot.
  const pending = useRef<Map<string, boolean>>(new Map());

  useEffect(() => {
    setRead(loadCache());
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/reads")
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((data: { paths: string[] }) => {
        if (cancelled) return;
        const next = new Set(data.paths);
        for (const [path, want] of pending.current) {
          if (want) next.add(path);
          else next.delete(path);
        }
        setRead(next);
        saveCache(next);
      })
      .catch(() => {
        // Keep cache; server unreachable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback(async (path: string, nextRead: boolean) => {
    pending.current.set(path, nextRead);
    setRead((prev) => {
      const next = new Set(prev);
      if (nextRead) next.add(path);
      else next.delete(path);
      saveCache(next);
      return next;
    });

    try {
      const res = await fetch("/api/reads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path, read: nextRead }),
      });
      if (!res.ok) throw new Error(String(res.status));
    } catch (err) {
      // Keep the optimistic state — localStorage caches it locally so the
      // user's intent is preserved across reloads even if the server write
      // fails. Cross-device sync is best-effort.
      console.warn("[useReadPages] failed to sync to server:", err);
    }
  }, []);

  return { read, toggle };
}
