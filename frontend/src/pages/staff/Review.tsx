import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Lock } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { DecisionStamp, StatusBadge } from "@/components/status";
import { ProcessingTimeline, STAGES } from "@/components/timeline";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { NativeSelect, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProgress } from "@/hooks/useProgress";
import { api, ApiError } from "@/lib/api";
import type { Check, Doc, HistoryItem, ProgressEvent, Version } from "@/lib/types";
import { CATEGORY_LABEL, cn, formatDate } from "@/lib/utils";

function refreshStaffViews(qc: QueryClient, applicationId: string) {
  for (const queryKey of [["staff-app", applicationId], ["dash"], ["queue"], ["metrics"], ["notifications"]]) {
    void qc.invalidateQueries({ queryKey });
  }
}

interface Identity { duplicate_accounts?: { application_id: string; reference: string; account_name: string; document_name: string | null; review_status?: string; claim_status: string }[]; claim_id: string; id_type: string; masked_value: string; status: string; other_account_claims: number; other_claim_name_agreement: (boolean | null)[]; confirmed_record: { held_by_this_account: boolean; name_agrees: boolean | null; source: string } | null }
export interface StaffApp {
  id: string; reference: string; created_at: string;
  applicant: { is_active?: boolean; id: string; full_name: string | null; email: string | null; name_masked: string; email_masked: string; email_verified: boolean; registered_at: string };
  current: Version; assigned_reviewer_id: string | null; assigned_reviewer: string | null; can_reveal: boolean;
  duplicate_documents?: { document_id: string; category: string; application_id: string; reference: string; account_name: string }[];
  identity: Identity[]; history: HistoryItem[]; events: ProgressEvent[]; approval_blockers: string[];
  reviews: { id: string; decision: string; reviewer: string | null; applicant_message: string | null; internal_note: string | null; requested_items: { category: string; instruction: string }[] | null; created_at: string; version_id: string }[];
  audit: { action: string; actor: string; actor_role: string | null; created_at: string; details: Record<string, unknown> }[];
}

const FIELD_LABEL: Record<string, string> = { full_name: "Name", date_of_birth: "Date of birth", id_number: "Identity number", pan_number: "PAN number on salary slip", employer_name: "Employer", gross_salary: "Gross salary", net_salary: "Net pay", pay_period: "Pay period" };
const RELEVANT: Record<string, string[]> = {
  AADHAAR: ["full_name", "date_of_birth", "id_number"], PAN: ["full_name", "date_of_birth", "id_number"],
  SALARY_SLIP: ["full_name", "pan_number", "employer_name", "pay_period", "gross_salary", "net_salary"], BANK_STATEMENT: ["full_name"],
};
const FIELD_COLOR: Record<string, string> = { full_name: "#5b3fa0", date_of_birth: "#a86a12", id_number: "#b42318", pan_number: "#b42318", employer_name: "#1f7a4d", gross_salary: "#12343b", net_salary: "#12343b", pay_period: "#4f6b6f" };

function docLabel(d: Doc, docs: Doc[]) {
  const same = docs.filter((x) => x.category === d.category);
  return CATEGORY_LABEL[d.category] + (same.length > 1 ? ` ${same.indexOf(d) + 1}` : "");
}

function Viewer({ doc, reveal, highlight }: { doc: Doc; reveal: boolean; highlight: string | null }) {
  const pages = doc.pages_processed?.length ? doc.pages_processed : [1];
  const [page, setPage] = useState(pages[0]);
  const prov = doc.extraction?.provenance ?? {};
  if (doc.purged) return <Alert tone="neutral">This file was deleted under the retention policy. Extracted values remain encrypted in the record.</Alert>;
  return (
    <div>
      {doc.page_count > 1 && (
        <div className="mb-2 flex flex-wrap gap-1" role="group" aria-label="Pages">
          {Array.from({ length: doc.page_count }, (_, i) => i + 1).map((p) => (
            <Button key={p} size="sm" variant={p === page ? "default" : "outline"} onClick={() => setPage(p)} aria-pressed={p === page}>
              Page {p}{doc.pages_processed && !doc.pages_processed.includes(p) ? " (not read)" : ""}
            </Button>
          ))}
        </div>
      )}
      <div className="relative inline-block max-w-full rounded border bg-white">
        <img key={`${doc.id}-${page}-${reveal}`} src={`/v1/documents/${doc.id}/pages/${page}${reveal ? "?reveal=true" : ""}`}
          alt={`${CATEGORY_LABEL[doc.category]} page ${page}${reveal ? " (unmasked)" : " (sensitive areas masked)"}`} className="block max-h-[70vh] max-w-full" />
        {Object.entries(prov).filter(([, p]) => p.page === page).map(([f, p]) => (
          <span key={f} aria-hidden
            className={cn("pointer-events-none absolute rounded-sm border-2 transition-opacity", highlight && highlight !== f ? "opacity-20" : "opacity-90")}
            style={{ left: `${p.bbox[0] * 100}%`, top: `${p.bbox[1] * 100}%`, width: `${(p.bbox[2] - p.bbox[0]) * 100}%`, height: `${(p.bbox[3] - p.bbox[1]) * 100}%`, borderColor: FIELD_COLOR[f] ?? "#12343b", margin: "-3px" }} />
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Boxes mark the lines each extracted field was read from. {reveal ? "Unmasked view — this access is recorded in the audit log." : "Identity numbers, dates of birth, addresses and contact details are blacked out."}
      </p>
    </div>
  );
}

export function Evidence({ doc, canReveal, showExact, setHighlight }: { doc: Doc; canReveal: boolean; showExact: boolean; setHighlight: (f: string | null) => void }) {
  const [revealed, setRevealed] = useState<Record<string, string | null> | null>(null);
  const ex = doc.extraction;
  const exact = useQuery({
    queryKey: ["document-exact", doc.id],
    queryFn: () => api<{ fields: Record<string, string | null> }>(`/v1/documents/${doc.id}/reveal`, { method: "POST" }),
    enabled: showExact && canReveal && !!ex,
    retry: false,
  });
  const fields = exact.data?.fields ?? revealed;
  const reveal = useMutation({
    mutationFn: () => api<{ fields: Record<string, string | null> }>(`/v1/documents/${doc.id}/reveal`, { method: "POST" }),
    onSuccess: (r) => setRevealed(r.fields),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not reveal"),
  });
  if (!ex) return <Alert tone={doc.error_code ? "warning" : "neutral"} title={doc.error_code ? "Manual review required" : "No extraction yet"}>{doc.error_message ?? "This document has not been processed."}</Alert>;
  const rows: [string, string | null][] = [
    ["full_name", fields?.full_name ?? ex.fields.full_name], ["date_of_birth", fields?.date_of_birth ?? ex.fields.date_of_birth],
    ["pan_number", fields?.pan_number ?? ex.fields.pan_number ?? null],
    ["id_number", fields?.id_number ?? ex.fields.id_number], ["employer_name", fields?.employer_name ?? ex.fields.employer_name],
    ["pay_period", ex.pay_period], ["gross_salary", ex.gross_salary && `${ex.currency ?? ""} ${ex.gross_salary}`],
    ["net_salary", ex.net_salary && `${ex.currency ?? ""} ${ex.net_salary}`],
  ];
  const fieldLabel = (key: string) => key === "id_number"
    ? doc.category === "AADHAAR" ? "Aadhaar number" : doc.category === "PAN" ? "PAN number" : "Identity number"
    : FIELD_LABEL[key] ?? key;
  const missing = ex.missing_fields.filter((f) => (RELEVANT[doc.category] ?? []).includes(f));
  return (
    <div className="space-y-3">
      {exact.isFetching && <p className="text-sm text-muted-foreground">Loading full document details…</p>}
      {exact.isError && <Alert tone="warning">Exact values could not be loaded. Try revealing them again below.</Alert>}
      <dl className="divide-y rounded-md border">
        {rows.filter(([k]) => (RELEVANT[doc.category] ?? []).includes(k)).map(([k, v]) => (
          <div key={k} className="grid grid-cols-[120px_1fr] gap-2 px-3 py-2 text-sm" onMouseEnter={() => setHighlight(k)} onMouseLeave={() => setHighlight(null)}
            onFocus={() => setHighlight(k)} onBlur={() => setHighlight(null)} tabIndex={0}>
            <dt className="flex items-center gap-2 text-muted-foreground"><span className="size-2.5 rounded-full" style={{ background: FIELD_COLOR[k] }} aria-hidden />{fieldLabel(k)}</dt>
            <dd className="font-medium break-words">{v ?? <span className="text-muted-foreground">Not found</span>}
              {ex.provenance?.[k] && <span className="ml-2 text-xs font-normal text-muted-foreground">page {ex.provenance[k].page}</span>}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>Classified as {CATEGORY_LABEL[ex.document_type] ?? ex.document_type}</span>·
        <span>{ex.extraction_method}{ex.ocr_confidence != null ? `, OCR confidence ${ex.ocr_confidence}%` : ", embedded text (no OCR confidence)"}</span>·
        <span>{ex.model_name} {ex.llm_latency_ms != null && `(${ex.llm_latency_ms} ms)`}</span>
      </div>
      {missing.length > 0 && <p className="text-xs text-warn">Not found on the document: {missing.map(fieldLabel).join(", ")}</p>}
      {canReveal ? (
        fields ? <p className="flex items-center gap-1 text-xs text-stamp"><Eye className="size-3.5" />Exact values shown. This reveal was recorded in the audit log.</p>
          : <Button size="sm" variant="outline" onClick={() => reveal.mutate()} disabled={reveal.isPending}><Eye />Reveal exact values</Button>
      ) : <p className="flex items-center gap-1 text-xs text-muted-foreground"><Lock className="size-3.5" />Claim this application to reveal exact values.</p>}
    </div>
  );
}

function NameMatrix({ v }: { v: Version }) {
  const docs = v.documents;
  const aadhaar = docs.find((d) => d.category === "AADHAAR");
  const pairChecks = v.checks.filter((c) => c.check_type.startsWith("NAME_"));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead className="text-left text-muted-foreground"><tr><th className="py-2 pr-3 font-medium">Document</th><th className="pr-3 font-medium">Name (masked)</th><th className="pr-3 font-medium">Compared with</th><th className="font-medium">Reviewer hints</th></tr></thead>
        <tbody className="divide-y">
          {aadhaar && <tr><td className="py-2 pr-3">{docLabel(aadhaar, docs)} (reference)</td><td className="pr-3">{aadhaar.extraction?.fields.full_name ?? "—"}</td><td className="pr-3"><StatusBadge value={aadhaar.extraction ? "MATCH" : "INCOMPLETE"} /></td><td /></tr>}
          {pairChecks.map((c) => {
            const d = docs.find((x) => x.id === c.document_id);
            const hints = (c.detail.hints as string[] | undefined) ?? [];
            return (
              <tr key={c.id}>
                <td className="py-2 pr-3">{d ? docLabel(d, docs) : c.check_type}</td>
                <td className="pr-3">{d?.extraction?.fields.full_name ?? "—"}</td>
                <td className="pr-3"><span className="block text-xs text-muted-foreground">{docs.find((x) => x.id === c.reference_document_id) ? docLabel(docs.find((x) => x.id === c.reference_document_id)!, docs) : "Aadhaar card"}</span><StatusBadge value={c.status} /><span className="block text-xs text-muted-foreground">{c.explanation}</span></td>
                <td className="text-xs">{hints.map((h) => h.replaceAll("_", " ").toLowerCase()).join("; ")}{c.detail.similarity != null && ` (similarity ${c.detail.similarity})`}{c.detail.name_agrees_with_aadhaar != null && (c.detail.name_agrees_with_aadhaar ? "name agrees" : "name differs")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-muted-foreground">Automatic match requires identical names after normalising case, spacing and punctuation. Hints such as reordered names or initials are for your judgement only.</p>
    </div>
  );
}

function OtherChecks({ checks, docs }: { checks: Check[]; docs: Doc[] }) {
  const rest = checks.filter((c) => !c.check_type.startsWith("NAME_"));
  return (
    <ul className="divide-y text-sm">
      {rest.map((c) => {
        const d = docs.find((x) => x.id === c.document_id);
        return (
          <li key={c.id} className="flex flex-wrap items-start justify-between gap-2 py-2">
            <div className="min-w-0 flex-1"><p className="font-medium">{c.check_type.replaceAll("_", " ").toLowerCase().replace(/^./, (s) => s.toUpperCase())}{d ? ` — ${docLabel(d, docs)}` : ""}</p>
              <p className="text-muted-foreground">{c.explanation}</p>
              {Object.keys(c.detail).length > 0 && <p className="text-xs text-muted-foreground">{Object.entries(c.detail).map(([k, val]) => `${k.replaceAll("_", " ")}: ${Array.isArray(val) ? val.join(", ") : String(val)}`).join(" · ")}</p>}
            </div>
            <StatusBadge value={c.status} />
          </li>
        );
      })}
    </ul>
  );
}

export function DecisionPanel({ a }: { a: StaffApp }) {
  const qc = useQueryClient();
  const v = a.current;
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [reason, setReason] = useState("DOCUMENTS_INCONSISTENT");
  const [items, setItems] = useState([{ category: "SALARY_SLIP", instruction: "" }]);
  const [open, setOpen] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => api(`/v1/staff/applications/${a.id}/${path}`, { json: body }),
    onSuccess: (_, vars) => { toast.success({ approve: "Approved", reject: "Rejected", "request-changes": "Changes requested", notes: "Note added", claim: "Assigned to you", release: "Released" }[vars.path] ?? "Done"); setOpen(null); setNote(""); setFormError(null); refreshStaffViews(qc, a.id); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Action failed"),
  });
  const base = { version_id: v.id, row_version: v.row_version };
  const sendDecision = (path: "reject" | "request-changes") => {
    if (msg.trim().length < 10) {
      setFormError("Enter a message of at least 10 characters explaining the reason to the applicant.");
      return;
    }
    if (path === "request-changes" && items.some((it) => it.instruction.trim().length < 5)) {
      setFormError("Enter a correction instruction of at least 5 characters for each document.");
      return;
    }
    setFormError(null);
    act.mutate({ path, body: { ...base, applicant_message: msg.trim(), internal_note: note.trim() || null,
      ...(path === "reject" ? { reason_code: reason } : { items: items.map((it) => ({ ...it, instruction: it.instruction.trim() })) }) } });
  };
  const dialogError = formError ?? (act.isError ? (act.error as Error).message : null);
  const decided = ["APPROVED", "REJECTED"].includes(v.review_status);
  const canAct = v.review_status === "PENDING_REVIEW" || ["FAILED", "NEEDS_REUPLOAD"].includes(v.processing_status);
  return (
    <section className="space-y-3 rounded-lg border bg-card p-4" aria-labelledby="decide-h">
      <h2 id="decide-h" className="text-lg font-semibold">{v.decision_source === "automatic" ? "Automatic decision" : "Human approval"}</h2>
      <p className="text-sm text-muted-foreground">Review the applicant details, submitted documents and name comparisons before recording your decision. A document match does not automatically approve an application.</p>
      {a.applicant.is_active === false && <Alert tone="danger" title="Account closed">Login is blocked. Review history has been retained.</Alert>}
      <DecisionStamp v={v} />
      {a.applicant.full_name && a.applicant.is_active !== false && <div className="flex flex-wrap gap-2">
        {[...(v.review_status === "APPROVED" ? [{ path: "require-reverification", label: "Cancel approval / require fresh documents", description: "Cancels the current approval and opens an empty draft. The applicant must upload Aadhaar, PAN and salary slips again. Previous decisions remain in history." }] : []),
          { path: "close-account", label: "Close account", description: "Blocks login and signs out all sessions. Current KYC approval is cancelled. Review history and duplicate identity records are retained. This closes the applicant account across its applications." }].map(action => (
          <Dialog key={action.path} open={open === action.path} onOpenChange={o => { setOpen(o ? action.path : null); setFormError(null); act.reset(); }}>
            <DialogTrigger asChild><Button variant="outline" disabled={act.isPending}>{action.label}</Button></DialogTrigger>
            <DialogContent>
              <DialogTitle>{action.label}</DialogTitle>
              <DialogDescription>{action.description}</DialogDescription>
              {dialogError && <Alert tone="danger">{dialogError}</Alert>}
              <Label htmlFor={`lifecycle-${action.path}`}>Reason shown to the applicant</Label>
              <Textarea id={`lifecycle-${action.path}`} maxLength={2000} value={msg} onChange={e => setMsg(e.target.value)} />
              <Button variant="destructive" disabled={act.isPending} onClick={() => {
                if (msg.trim().length < 10) { setFormError("Enter a reason of at least 10 characters."); return; }
                act.mutate({ path: action.path, body: { ...base, applicant_message: msg.trim() } });
              }}>{act.isPending ? "Saving…" : "Confirm"}</Button>
            </DialogContent>
          </Dialog>
        ))}
      </div>}
      {a.identity?.some((i) => i.status === "UNTRUSTED") && <Alert tone="warning" title="Identifier validity warnings">
        Some identifiers are sample values, unreadable or fail format checks. These warnings do not change the name/PAN consistency result.
        Approval confirms document consistency only; these identifiers remain unvalidated and are not reserved as verified identities.
      </Alert>}
      {!decided && (a.approval_blockers.length ? (
        <Alert tone="warning" title="Approval is not available"><ul className="list-disc pl-4">{a.approval_blockers.map((b) => <li key={b}>{b}</li>)}</ul></Alert>
      ) : <Alert tone="success">Automated checks agree. Review the evidence before approving.</Alert>)}
      <div className="flex flex-wrap gap-2">
        <Button variant="stamp" disabled={decided || a.approval_blockers.length > 0 || act.isPending} onClick={() => act.mutate({ path: "approve", body: { ...base, internal_note: note || null } })}>Approve</Button>
        <Dialog open={open === "changes"} onOpenChange={(o) => { setOpen(o ? "changes" : null); setFormError(null); act.reset(); }}>
          <DialogTrigger asChild><Button variant="outline" disabled={decided || !canAct}>Request changes</Button></DialogTrigger>
          <DialogContent>
            <DialogTitle>Request changes</DialogTitle>
            <DialogDescription>The applicant sees your message and each requested item, then uploads a corrected version for reprocessing.</DialogDescription>
            {dialogError && <Alert tone="danger">{dialogError}</Alert>}
            {items.map((it, i) => (
              <div key={i} className="grid gap-2 sm:grid-cols-[150px_1fr]">
                <NativeSelect aria-label="Document" value={it.category} onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, category: e.target.value } : x))}>
                  {["AADHAAR", "PAN", "SALARY_SLIP", "BANK_STATEMENT", "OTHER"].map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                </NativeSelect>
                <Textarea aria-label="What to upload or correct" maxLength={500} className="min-h-10" value={it.instruction} placeholder="e.g. Upload a clearer photo of the Aadhaar card" onChange={(e) => setItems(items.map((x, j) => j === i ? { ...x, instruction: e.target.value } : x))} />
              </div>
            ))}
            <p className="text-xs text-muted-foreground">Each correction instruction needs at least 5 characters.</p>
            <Button variant="link" size="sm" disabled={items.length >= 10} onClick={() => setItems([...items, { category: "SALARY_SLIP", instruction: "" }])}>Add another item</Button>
            <Label htmlFor="cmsg">Message to the applicant</Label>
            <Textarea id="cmsg" maxLength={2000} value={msg} onChange={(e) => setMsg(e.target.value)} />
            <p className="text-xs text-muted-foreground">Required: at least 10 characters. Explain what the applicant needs to change.</p>
            <Button disabled={act.isPending} onClick={() => sendDecision("request-changes")}>{act.isPending ? "Sending…" : "Send request"}</Button>
          </DialogContent>
        </Dialog>
        <Dialog open={open === "reject"} onOpenChange={(o) => { setOpen(o ? "reject" : null); setFormError(null); act.reset(); }}>
          <DialogTrigger asChild><Button variant="outline" className="text-bad" disabled={decided || v.review_status !== "PENDING_REVIEW"}>Reject</Button></DialogTrigger>
          <DialogContent>
            <DialogTitle>Reject application</DialogTitle>
            <DialogDescription>A reason is required. The message below is shown to the applicant; internal notes are not.</DialogDescription>
            {dialogError && <Alert tone="danger">{dialogError}</Alert>}
            <Label htmlFor="reason">Reason</Label>
            <NativeSelect id="reason" value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="DOCUMENTS_INCONSISTENT">Documents inconsistent</option><option value="IDENTITY_NOT_ESTABLISHED">Identity not established</option>
              <option value="SUSPECTED_ALTERED_DOCUMENT">Suspected altered document</option><option value="DUPLICATE_ACCOUNT">Duplicate account</option><option value="OTHER">Other</option>
            </NativeSelect>
            <Label htmlFor="rmsg">Message to the applicant</Label>
            <Textarea id="rmsg" maxLength={2000} value={msg} onChange={(e) => setMsg(e.target.value)} />
            <p className="text-xs text-muted-foreground">Required: at least 10 characters explaining why the application is rejected.</p>
            <Button variant="destructive" disabled={act.isPending} onClick={() => sendDecision("reject")}>{act.isPending ? "Rejecting…" : "Reject application"}</Button>
          </DialogContent>
        </Dialog>
      </div>
      <div className="space-y-1.5 border-t pt-3">
        <Label htmlFor="note">Internal note (staff only)</Label>
        <Textarea id="note" maxLength={4000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Visible to Northbridge staff only" />
        <Button size="sm" variant="secondary" disabled={!note.trim()} onClick={() => act.mutate({ path: "notes", body: { version_id: v.id, note } })}>Add note</Button>
      </div>
    </section>
  );
}

export function ReviewPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["staff-app", id], queryFn: () => api<StaffApp>(`/v1/staff/applications/${id}`), refetchInterval: 5000 });
  const progress = useProgress(id);
  const [docId, setDocId] = useState<string | null>(null);
  const [revealOverride, setReveal] = useState<boolean | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const assign = useMutation({
    mutationFn: (path: "claim" | "release") => api(`/v1/staff/applications/${id}/${path}`, { method: "POST" }),
    onSuccess: () => refreshStaffViews(qc, id!),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });
  const reprocess = useMutation({ mutationFn: () => api(`/v1/staff/applications/${id}/reprocess`, { method: "POST" }), onSuccess: () => { toast.success("Reprocessing queued"); refreshStaffViews(qc, id!); }, onError: (e) => toast.error((e as Error).message) });
  if (q.isLoading) return <Skeleton className="h-[60vh]" />;
  if (!q.data) return <Alert tone="danger">This application could not be loaded, or you do not have access to it.</Alert>;
  const a = q.data;
  const v = a.current;
  const doc = v.documents.find((d) => d.id === docId) ?? v.documents[0];
  const mine = a.can_reveal;
  const isAdmin = !!a.applicant.full_name;
  const reveal = revealOverride ?? isAdmin;
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">{a.reference}</h1>
          <p className="text-sm text-muted-foreground">{a.applicant.full_name ?? a.applicant.name_masked} · {a.applicant.email ?? a.applicant.email_masked} · version {v.version_number} · submitted {formatDate(v.submitted_at)}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span>Processing</span><StatusBadge value={v.processing_status} />
            <span className="ml-2">Automated check</span><StatusBadge value={v.check_status} />
            <span className="ml-2">Review</span><StatusBadge value={v.review_status} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">{a.assigned_reviewer ? `Assigned to ${a.assigned_reviewer}` : "Unassigned"}</span>
          {a.assigned_reviewer_id && mine ? <Button variant="outline" size="sm" onClick={() => assign.mutate("release")}>Release</Button>
            : <Button size="sm" onClick={() => assign.mutate("claim")} disabled={!!a.assigned_reviewer_id}>Claim review</Button>}
          {v.state === "SUBMITTED" && !["APPROVED", "REJECTED"].includes(v.review_status)
            && !["QUEUED", "RUNNING"].includes(v.processing_status)
            && <Button size="sm" variant="outline" onClick={() => reprocess.mutate()} disabled={reprocess.isPending}>{v.processing_status === "FAILED" || v.documents.some((d) => d.processing_status === "FAILED") ? "Retry processing" : "Reprocess documents"}</Button>}
          <Button asChild size="sm" variant="ghost"><a href={`/v1/staff/applications/${a.id}/export.csv`}>Export metadata</a></Button>
        </div>
      </header>

      <section className="rounded-lg border bg-card p-4" aria-labelledby="applicant-details-h">
        <h2 id="applicant-details-h" className="text-lg font-semibold">Applicant details</h2>
        <dl className="mt-3 grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div><dt className="text-muted-foreground">Full name</dt><dd className="font-medium">{a.applicant.full_name ?? a.applicant.name_masked}</dd></div>
          <div><dt className="text-muted-foreground">Email address</dt><dd className="break-all font-medium">{a.applicant.email ?? a.applicant.email_masked}</dd></div>
          <div><dt className="text-muted-foreground">Email verification</dt><dd>{a.applicant.email_verified ? "Verified" : "Not verified"}</dd></div>
          <div><dt className="text-muted-foreground">Registered</dt><dd>{formatDate(a.applicant.registered_at)}</dd></div>
          <div><dt className="text-muted-foreground">Application created</dt><dd>{formatDate(a.created_at)}</dd></div>
          <div><dt className="text-muted-foreground">Submitted</dt><dd>{formatDate(v.submitted_at)}</dd></div>
          <div><dt className="text-muted-foreground">Applicant ID</dt><dd className="break-all">{a.applicant.id}</dd></div>
          <div><dt className="text-muted-foreground">Review status</dt><dd><StatusBadge value={v.review_status} /></dd></div>
          <div><dt className="text-muted-foreground">Decision date</dt><dd>{formatDate(v.decided_at)}</dd></div>
        </dl>
      </section>

      <section className="rounded-lg border bg-card p-4" aria-labelledby="submitted-documents-h">
        <h2 id="submitted-documents-h" className="text-lg font-semibold">Submitted documents ({v.documents.length})</h2>
        <ul className="mt-3 divide-y">
          {v.documents.map((d) => (
            <li key={d.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="space-y-1">
                <Button variant="link" className="h-auto p-0" onClick={() => setDocId(d.id)}>{docLabel(d, v.documents)}</Button>
                <p className="text-xs text-muted-foreground">{d.file_format.toUpperCase()} · {d.page_count} page(s) · uploaded {formatDate(d.uploaded_at)}</p>
                <p className="text-sm">Current stage: {STAGES.find((s) => s.key === d.current_stage)?.label ?? d.current_stage ?? "Waiting to start"}</p>
                {d.pages_processed && <p className="text-xs text-muted-foreground">Pages read: {d.pages_processed.join(", ")} of {d.page_count}</p>}
                {d.error_message && <p className="text-sm text-bad">{d.error_message}</p>}
                {d.attention && <p className="text-sm text-warn">{d.attention}</p>}
                {d.quality_suggestions.map((suggestion) => <p key={suggestion} className="text-xs text-muted-foreground">{suggestion}</p>)}
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge value={d.processing_status} />
                {mine && !d.purged && <Button asChild size="sm" variant="outline"><a href={`/v1/documents/${d.id}/original`} target="_blank" rel="noreferrer">Open original</a></Button>}
              </div>
            </li>
          ))}
        </ul>
        {!v.documents.length && <p className="mt-3 text-sm text-muted-foreground">No documents uploaded.</p>}
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section aria-label="Document viewer" className="rounded-lg border bg-card p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Documents">
              {v.documents.map((d) => (
                <Button key={d.id} role="tab" aria-selected={d.id === doc?.id} size="sm" variant={d.id === doc?.id ? "default" : "outline"} onClick={() => setDocId(d.id)}
                  className={d.attention ? "ring-1 ring-warn" : ""}>{docLabel(d, v.documents)}</Button>
              ))}
            </div>
            {mine && <Button size="sm" variant="ghost" onClick={() => setReveal(!reveal)} aria-pressed={reveal}>{reveal ? <EyeOff /> : <Eye />}{reveal ? "Mask" : "Show unmasked"}</Button>}
          </div>
          {doc ? <>
            {doc.quality_flags.length > 0 && <Alert tone="warning" className="mb-3" title="Scan quality warnings">{doc.quality_flags.join(", ").toLowerCase().replaceAll("_", " ")}</Alert>}
            <Viewer key={doc.id} doc={doc} reveal={reveal} highlight={highlight} />
          </> : <p className="text-sm text-muted-foreground">No documents uploaded.</p>}
        </section>

        <div className="space-y-5">
          <Tabs defaultValue="evidence">
            <TabsList>
              <TabsTrigger value="evidence">Extracted fields</TabsTrigger>
              <TabsTrigger value="names">Name comparison</TabsTrigger>
              <TabsTrigger value="checks">Other checks</TabsTrigger>
              <TabsTrigger value="identity">Identity</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
            </TabsList>
            <TabsContent value="evidence">{doc && <Evidence key={doc.id} doc={doc} canReveal={mine} showExact={isAdmin} setHighlight={setHighlight} />}</TabsContent>
            <TabsContent value="names"><NameMatrix v={v} /></TabsContent>
            <TabsContent value="checks"><OtherChecks checks={v.checks} docs={v.documents} /></TabsContent>
            <TabsContent value="identity">
              {a.identity.length ? a.identity.map((i) => (
                <div key={i.claim_id} className="mb-3 rounded-md border p-3 text-sm">
                  <p className="font-medium">{i.id_type} {i.masked_value} {i.status === "UNTRUSTED" ? <Badge tone="warning">Not validated — informational</Badge> : <StatusBadge value={i.status === "CONFLICT" || i.status === "REJECTED" ? "MISMATCH" : "MATCH"} />}</p>
                  <p className="text-muted-foreground">Claim status: {i.status.toLowerCase()}. {i.other_account_claims ? `${i.other_account_claims} other account(s) submitted this identifier; extracted names ${i.other_claim_name_agreement.every(Boolean) ? "agree" : "do not all agree"}.` : "No other account has submitted it."}</p>
                  {i.duplicate_accounts?.length ? <ul className="mt-2 space-y-2 border-t pt-2">
                    {i.duplicate_accounts.map((other) => <li key={other.application_id}>
                      <a className="text-stamp underline" href={`/staff/applications/${other.application_id}`}>{other.reference}</a>
                      <span> — {other.account_name} · {other.review_status ?? other.claim_status}</span>
                      {other.document_name && <p>Extracted document name: {other.document_name}</p>}
                    </li>)}
                  </ul> : null}
                  {i.confirmed_record && <p className="text-muted-foreground">Staff-confirmed record: {i.confirmed_record.held_by_this_account ? "held by this account" : "held by another account"}; name {i.confirmed_record.name_agrees ? "agrees" : "differs"}.</p>}
                </div>
              )) : <p className="text-sm text-muted-foreground">No identifiers were extracted yet.</p>}
              {a.duplicate_documents?.length ? <section className="my-3 rounded-md border border-warn/40 p-3">
                <h3 className="font-semibold">Identical files uploaded by other accounts</h3>
                <ul className="mt-2 space-y-2 text-sm">{a.duplicate_documents.map((duplicate, index) => <li key={`${duplicate.document_id}-${index}`}>
                  {CATEGORY_LABEL[duplicate.category]} · {duplicate.account_name} · <a className="text-stamp underline" href={`/staff/applications/${duplicate.application_id}`}>{duplicate.reference}</a>
                </li>)}</ul>
              </section> : null}
              <p className="text-xs text-muted-foreground">Only another account’s current approved submission triggers duplicate conflict or rejection. Other uploads are shown for information. Identifiers are matched by keyed fingerprint; format checks do not verify government records.</p>
            </TabsContent>
            <TabsContent value="history">
              <ul className="space-y-2 text-sm">
                {a.reviews.map((r) => (
                  <li key={r.id} className="rounded-md border p-3">
                    <p className="font-medium">{r.decision === "NOTE" ? "Internal note" : r.decision.replaceAll("_", " ").toLowerCase()} — {r.reviewer} · {formatDate(r.created_at)}</p>
                    {r.applicant_message && <p>To applicant: {r.applicant_message}</p>}
                    {r.internal_note && <p className="text-muted-foreground">Internal: {r.internal_note}</p>}
                  </li>
                ))}
                {a.audit.slice(0, 40).map((e, i) => (
                  <li key={i} className="flex gap-2 text-xs text-muted-foreground"><time className="shrink-0 tabular-nums">{formatDate(e.created_at)}</time><span>{e.actor} ({e.actor_role ?? "system"}): {e.action}</span></li>
                ))}
              </ul>
            </TabsContent>
          </Tabs>
          <DecisionPanel a={a} />
          <ProcessingTimeline events={progress.events.length ? progress.events : a.events} version={v} docs={v.documents} mode={progress.mode} staff />
        </div>
      </div>
    </div>
  );
}
