import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ProgressEvent } from "@/lib/types";

type Mode = "connecting" | "live" | "polling" | "offline";

/**
 * Real backend progress for one application. Uses authenticated SSE (cookie, no secrets in the
 * URL) with Last-Event-ID resume; falls back to polling if the stream keeps failing.
 * Events are persisted server-side, so a refresh reloads the full history.
 */
export function useProgress(appId: string | undefined, { pollMs = 3000 } = {}) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [mode, setMode] = useState<Mode>("connecting");
  const lastId = useRef(0);
  const qc = useQueryClient();

  useEffect(() => {
    if (!appId) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    lastId.current = 0;
    setEvents([]);

    const add = (incoming: ProgressEvent[]) => {
      if (!incoming.length) return;
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        const merged = [...prev, ...incoming.filter((e) => !seen.has(e.id))].sort((a, b) => a.id - b.id);
        lastId.current = merged.at(-1)?.id ?? lastId.current;
        return merged;
      });
    };
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ["application"] });
      qc.invalidateQueries({ queryKey: ["staff-app", appId] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      for (const key of ["dash", "queue", "metrics"]) void qc.invalidateQueries({ queryKey: [key] });
    };

    const poll = async () => {
      if (cancelled) return;
      try {
        const r = await api<{ events: ProgressEvent[] }>(`/v1/applications/${appId}/events?after=${lastId.current}`);
        if (r.events.length) refresh();
        add(r.events);
        setMode("polling");
      } catch {
        setMode("offline");
      }
      pollTimer = setTimeout(poll, pollMs);
    };

    const connect = () => {
      if (typeof EventSource === "undefined") return void poll();
      es = new EventSource(`/v1/applications/${appId}/events/stream?after=${lastId.current}`, { withCredentials: true });
      es.onopen = () => { failures = 0; setMode("live"); };
      es.addEventListener("progress", (m) => add([JSON.parse((m as MessageEvent).data)]));
      es.addEventListener("status", () => refresh());
      es.onerror = () => {
        failures += 1;
        if (failures >= 3) {  // the browser retries on its own first; then switch to polling
          es?.close();
          es = null;
          void poll();
        } else {
          setMode("connecting");
        }
      };
    };

    // Load persisted history first, then go live.
    api<{ events: ProgressEvent[] }>(`/v1/applications/${appId}/events?after=0`)
      .then((r) => { if (!cancelled) { add(r.events); connect(); } })
      .catch(() => { if (!cancelled) void poll(); });

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [appId, pollMs, qc]);

  return { events, mode };
}
