import { CheckCircle2, XCircle } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { DecisionStamp } from "@/components/status";
import type { Version } from "@/lib/types";
import { CATEGORY_LABEL, formatDate } from "@/lib/utils";

export function ApplicantResult({ version: v }: { version: Version }) {
  if (v.review_status === "APPROVED") return (
    <section aria-live="polite" className="space-y-3 rounded-lg border border-ok/40 bg-ok-soft p-5">
      <div className="flex items-center gap-3">
        <CheckCircle2 className="size-8 shrink-0 text-ok" aria-hidden />
        <h1 className="text-2xl font-semibold">KYC completed and approved</h1>
      </div>
      <DecisionStamp v={v} animate />
      <p>Northbridge has reviewed and approved your submitted documents. No further action is needed for this submission.</p>
      {v.decided_at && <p className="text-sm text-muted-foreground">Approved on {formatDate(v.decided_at)}</p>}
      <p className="text-xs text-muted-foreground">Approval confirms document name and PAN-number consistency. It does not authenticate government records or approve a loan.</p>
    </section>
  );
  if (v.review_status === "REJECTED") return (
    <section aria-live="polite" className="space-y-3 rounded-lg border border-bad/40 bg-bad-soft p-5">
      <div className="flex items-center gap-3">
        <XCircle className="size-8 shrink-0 text-bad" aria-hidden />
        <h1 className="text-2xl font-semibold">KYC application rejected</h1>
      </div>
      <DecisionStamp v={v} animate />
      <p>{v.decision_source === "automatic" ? "This submission was automatically rejected because its documents or identifiers were already used for another account. See the reason below." : "Northbridge has reviewed your submission and has not approved it. See the reviewer’s reason below."}</p>
    </section>
  );
  if (v.status.key === "IDENTITY_CONFLICT") return (
    <section aria-live="polite" className="space-y-3 rounded-lg border border-warn/40 bg-warn-soft p-5">
      <h1 className="text-2xl font-semibold">Identity document conflict - staff review required</h1>
      <p>{v.status.detail}</p>
      <Alert tone="warning" title="Document ownership caution">
        Only submit documents that belong to you or that you are authorized to provide. Do not use another person's identity
        or submit forged or altered documents. A conflict alone does not prove misuse.
      </Alert>
    </section>
  );
  const pending = ["QUEUED", "RUNNING"].includes(v.processing_status);
  const draft = v.state === "DRAFT";
  const match = !pending && !draft && v.check_status === "MATCH";
  const mismatch = !pending && !draft && v.check_status === "MISMATCH";
  const reasons = !pending && !draft && !match
    ? v.checks.filter((c) => ["MISMATCH", "INCOMPLETE"].includes(c.status) && c.check_type !== "SALARY_REVIEW_INFO")
    : [];
  const headline = draft ? "Upload your documents" : pending ? "Your documents have been received"
    : match ? "Your documents match" : mismatch ? "Your documents do not match" : "A match could not be confirmed";
  const detail = draft ? "Upload your documents and submit them for a name consistency check."
    : pending ? "Your result will appear here when it is ready."
    : match ? "The names on your Aadhaar, PAN card and salary slips agree, and each salary slip contains the matching PAN number."
    : mismatch ? "The following issues were found in your documents."
    : "We could not confirm whether the names on your documents match. Staff will review your submission.";
  return (
    <section aria-live="polite" className="space-y-2 rounded-lg border bg-card p-5">
      <h1 className="text-2xl font-semibold">{headline}</h1>
      <p className="text-muted-foreground">{detail}</p>
      {reasons.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">
        {reasons.map((c) => {
          const doc = v.documents.find((d) => d.id === c.document_id);
          const peers = v.documents.filter((d) => d.category === doc?.category);
          return <li key={c.id}>{doc && <strong>{CATEGORY_LABEL[doc.category]}{peers.length > 1 ? ` ${peers.indexOf(doc) + 1}` : ""}: </strong>}{c.explanation}</li>;
        })}
      </ul>}
    </section>
  );
}
