import { Badge } from "@/components/ui/badge";
import type { Status, Version } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const TONE: Record<string, "neutral" | "info" | "success" | "warning" | "danger" | "stamp"> = {
  MATCH: "success", MISMATCH: "danger", INCOMPLETE: "warning", NOT_APPLICABLE: "neutral",
  NOT_STARTED: "neutral", QUEUED: "info", RUNNING: "info", SUCCEEDED: "success", FAILED: "danger", NEEDS_REUPLOAD: "warning",
  NOT_READY: "neutral", PENDING_REVIEW: "info", APPROVED: "stamp", REJECTED: "danger", CHANGES_REQUESTED: "warning",
  DRAFT: "neutral", SUBMITTED: "info", SUPERSEDED: "neutral",
};

const WORDS: Record<string, string> = {
  NOT_STARTED: "Not started", QUEUED: "Queued", RUNNING: "Running", SUCCEEDED: "Processed", FAILED: "Failed",
  NEEDS_REUPLOAD: "Needs re-upload", MATCH: "Match", MISMATCH: "Mismatch", INCOMPLETE: "Incomplete",
  NOT_APPLICABLE: "Not applicable", NOT_READY: "Not ready", PENDING_REVIEW: "Pending review", APPROVED: "Approved",
  REJECTED: "Rejected", CHANGES_REQUESTED: "Changes requested", DRAFT: "Draft", SUBMITTED: "Submitted", SUPERSEDED: "Superseded",
};

export function StatusBadge({ value, className }: { value?: string | null; className?: string }) {
  if (!value) return <Badge className={className}>—</Badge>;
  return <Badge tone={TONE[value] ?? "neutral"} className={className}>{WORDS[value] ?? value}</Badge>;
}

/** The human decision is a stamp; nothing automated ever renders one. */
export function DecisionStamp({ v, animate = false }: { v: Pick<Version, "review_status" | "decided_at" | "decision_source">; animate?: boolean }) {
  if (v.review_status !== "APPROVED" && v.review_status !== "REJECTED") return null;
  const approved = v.review_status === "APPROVED";
  return (
    <div className={cn("nb-stamp", approved ? "text-stamp" : "text-bad", animate && "nb-stamp-enter")} aria-label={approved ? "Approved by Northbridge" : "Not approved by Northbridge"}>
      <span className="text-[15px] leading-none">{approved ? "Approved" : "Rejected"}</span>
      <span className="text-[11px] font-normal leading-none">{v.decision_source === "automatic" ? "Automatic document check" : "Northbridge review"} · {formatDate(v.decided_at)}</span>
    </div>
  );
}

export function StatusHero({ status, version }: { status: Status; version: Version }) {
  const decided = version.review_status === "APPROVED" || version.review_status === "REJECTED";
  return (
    <section aria-live="polite" className="flex flex-col gap-4 rounded-lg border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
      <div className="max-w-2xl space-y-2">
        <h1 className="text-2xl font-semibold">{status.headline}</h1>
        <p className="text-muted-foreground">{status.detail}</p>
        {version.check_status && (
          <p className="nb-typed text-sm">
            Automated document check: <strong>{WORDS[version.check_status]}</strong>
            {version.processed_at ? ` (${formatDate(version.processed_at)})` : ""}
            {!decided && version.check_status === "MATCH" && " — still needs a Northbridge reviewer."}
          </p>
        )}
      </div>
      <DecisionStamp v={version} animate />
    </section>
  );
}
