import { AlertCircle, CheckCircle2, Circle, Loader2, Radio, WifiOff } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import type { Doc, ProgressEvent, Version } from "@/lib/types";
import { CATEGORY_LABEL, cn, formatDate, formatTime } from "@/lib/utils";

export const STAGES: { key: string; label: string; perDoc?: boolean }[] = [
  { key: "UPLOAD_RECEIVED", label: "Upload received" },
  { key: "FILE_VALIDATION", label: "File validation", perDoc: true },
  { key: "QUALITY_INSPECTION", label: "Quality inspection", perDoc: true },
  { key: "TEXT_EXTRACTION", label: "Text extraction / OCR", perDoc: true },
  { key: "OCR_SCHEMA_VALIDATION", label: "Pydantic schema check (extracted text / OCR)", perDoc: true },
  { key: "PII_MASKING", label: "Personal data masking", perDoc: true },
  { key: "LLM_EXTRACTION", label: "Structured extraction (Gemini, masked text only)", perDoc: true },
  { key: "LLM_TELEMETRY", label: "Gemini telemetry (latency and token usage)", perDoc: true },
  { key: "SCHEMA_VALIDATION", label: "Schema and evidence validation", perDoc: true },
  { key: "DUPLICATE_CHECK", label: "Identity duplicate check" },
  { key: "NAME_CHECKS", label: "Name consistency checks" },
  { key: "AWAITING_REVIEW", label: "Awaiting Northbridge review" },
  { key: "DECISION", label: "Reviewer decision" },
];

type State = "done" | "active" | "failed" | "todo";

function stageState(key: string, evs: ProgressEvent[], v: Version, docs: Doc[], perDoc?: boolean): { state: State; note?: string } {
  const mine = evs.filter((e) => e.stage === key);
  if (key === "UPLOAD_RECEIVED") return { state: docs.length ? "done" : "todo", note: `${docs.length} document(s)` };
  if (key === "AWAITING_REVIEW") {
    if (v.review_status === "PENDING_REVIEW") return { state: "active" };
    return { state: mine.length ? "done" : "todo" };
  }
  if (key === "DECISION") {
    const decided = ["APPROVED", "REJECTED", "CHANGES_REQUESTED"].includes(v.review_status);
    return { state: decided ? "done" : "todo" };
  }
  if (perDoc) {
    const ids = new Set(docs.map((d) => d.id));
    const done = new Set(mine.filter((e) => e.status === "succeeded").map((e) => e.document_id));
    const failed = new Set(mine.filter((e) => e.status === "failed").map((e) => e.document_id));
    const started = new Set(mine.map((e) => e.document_id));
    // Older attempts can contain a start without a closing event when the
    // provider returned invalid responses. A terminal document is not loading.
    for (const doc of docs) {
      if (started.has(doc.id) && !done.has(doc.id)
        && ["FAILED", "NEEDS_REUPLOAD"].includes(doc.processing_status)) failed.add(doc.id);
    }
    const n = ids.size;
    if (failed.size) return { state: "failed", note: `${done.size} of ${n} done, ${failed.size} need attention` };
    if (n && done.size >= n) return { state: "done", note: `${n} of ${n} documents` };
    if (started.size || done.size) return { state: "active", note: `${done.size} of ${n} documents` };
    return { state: "todo" };
  }
  if (mine.some((e) => e.status === "failed")) return { state: "failed" };
  if (mine.some((e) => e.status === "succeeded")) return { state: "done" };
  if (mine.length) return { state: "active" };
  return { state: "todo" };
}

const ICON: Record<State, React.ReactNode> = {
  done: <CheckCircle2 className="size-5 text-ok" aria-hidden />,
  active: <Loader2 className="size-5 animate-spin text-stamp" aria-hidden />,
  failed: <AlertCircle className="size-5 text-warn" aria-hidden />,
  todo: <Circle className="size-5 text-input" aria-hidden />,
};
const SR: Record<State, string> = { done: "complete", active: "in progress", failed: "needs attention", todo: "not started" };

export function ConnectionPill({ mode }: { mode: string }) {
  const map: Record<string, [React.ReactNode, string]> = {
    live: [<Radio className="size-3.5 text-ok" key="i" />, "Live updates"],
    polling: [<Radio className="size-3.5 text-warn" key="i" />, "Updating every few seconds"],
    connecting: [<Loader2 className="size-3.5 animate-spin" key="i" />, "Connecting…"],
    offline: [<WifiOff className="size-3.5 text-bad" key="i" />, "Offline — retrying"],
  };
  const [icon, text] = map[mode] ?? map.connecting;
  return <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="conn-mode">{icon}{text}</span>;
}

function OcrSchemaView() {
  const schema = useQuery({ queryKey: ["processing-schema"], queryFn: () => api<Record<string, unknown>>("/v1/staff/processing-schema"), staleTime: Infinity });
  return <div className="mt-2 space-y-2">
    <p>Checks page references, extraction method, word text types, normalized bounding boxes and confidence ranges. Raw extracted text stays local; this is the schema definition, not applicant data.</p>
    {schema.isPending ? <p>Loading schema…</p> : schema.isError ? <p role="alert">Could not load the schema. Close and reopen to retry.</p> : <pre className="max-h-80 overflow-auto rounded border bg-muted p-3 text-xs">{JSON.stringify(schema.data, null, 2)}</pre>}
  </div>;
}

export function ProcessingTimeline({ events, version, docs, mode, staff = false }: {
  events: ProgressEvent[]; version: Version; docs: Doc[]; mode: string; staff?: boolean;
}) {
  const [showLog, setShowLog] = useState(false);
  const [showSchema, setShowSchema] = useState(false);
  const attemptEvents = events.filter((e) => e.version_id === version.id && (e.attempt === version.processing_attempt || e.stage === "DECISION"));
  const inProgress = version.state !== "DRAFT";
  const docName = (id: string | null) => {
    const d = docs.find((x) => x.id === id);
    return d ? CATEGORY_LABEL[d.category] : "";
  };
  return (
    <section aria-labelledby="timeline-h" className="rounded-lg border bg-card p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 id="timeline-h" className="text-lg font-semibold">Processing</h2>
        <ConnectionPill mode={mode} />
      </div>
      {!inProgress ? (
        <p className="text-sm text-muted-foreground">Processing starts when you submit. Each step will appear here as it actually happens.</p>
      ) : (
        <ol className="space-y-0">
          {STAGES.map((s, i) => {
            const st = stageState(s.key, attemptEvents, version, docs, s.perDoc);
            const last = [...attemptEvents].reverse().find((e) => e.stage === s.key);
            return (
              <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0" data-stage={s.key} data-state={st.state}>
                {i < STAGES.length - 1 && <span className={cn("absolute left-[9px] top-6 h-[calc(100%-18px)] w-px", st.state === "done" ? "bg-ok/40" : "bg-border")} aria-hidden />}
                <span className="relative z-10 bg-card">{ICON[st.state]}</span>
                <div className="min-w-0 flex-1">
                  <p className={cn("text-sm", st.state === "todo" ? "text-muted-foreground" : "font-medium")}>
                    <span className="text-muted-foreground tabular-nums">{i + 1}.</span> {s.label}
                    <span className="sr-only"> — {SR[st.state]}</span>
                  </p>
                  {(st.note || last) && st.state !== "todo" && (
                    <p className="text-xs text-muted-foreground">
                      {st.note}{st.note && last ? " · " : ""}{last ? `${last.message} (${formatTime(last.created_at)})` : ""}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {staff && <div className="mt-4 border-t pt-3 text-sm">
        <Button variant="link" size="sm" aria-expanded={showSchema} onClick={() => setShowSchema(!showSchema)}>{showSchema ? "Hide Pydantic OCR schema" : "View Pydantic OCR schema"}</Button>
        {showSchema && <OcrSchemaView />}
      </div>}
      {attemptEvents.length > 0 && (
        <div className="mt-4 border-t pt-3">
          <Button variant="link" size="sm" onClick={() => setShowLog((x) => !x)} aria-expanded={showLog}>
            {showLog ? "Hide" : "Show"} detailed activity ({attemptEvents.length} events)
          </Button>
          {showLog && (
            <ul className="mt-2 max-h-80 space-y-1.5 overflow-y-auto text-xs" data-testid="event-log">
              {attemptEvents.map((e) => (
                <li key={e.id} className="flex gap-2">
                  <time className="shrink-0 tabular-nums text-muted-foreground">{formatDate(e.created_at)}</time>
                  <span className={e.status === "failed" ? "text-warn" : ""}>{e.message}{staff && e.document_id && !e.message.startsWith(docName(e.document_id)) ? ` [${docName(e.document_id)}]` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
