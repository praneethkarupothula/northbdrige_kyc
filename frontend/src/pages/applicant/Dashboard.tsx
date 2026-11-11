import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, MinusCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApplicantResult } from "@/components/applicant-result";
import { DocumentRow, DropZone } from "@/components/uploader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/misc";
import { useMe } from "@/hooks/useAuth";
import { api, ApiError } from "@/lib/api";
import type { Application, Version } from "@/lib/types";
import { CATEGORY_LABEL } from "@/lib/utils";

export function useApplication() {
  return useQuery({
    queryKey: ["application"],
    queryFn: () => api<Application>("/v1/applications", { method: "POST" }),
    refetchInterval: 5000,
  });
}

function Section({ title, required, done, children, aside }: {
  title: string; required: string; done: boolean | null; children: React.ReactNode; aside?: React.ReactNode;
}) {
  const Icon = done === null ? MinusCircle : done ? CheckCircle2 : Circle;
  return (
    <section className="border-t py-5 first:border-t-0 first:pt-0">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-2 font-sans text-base font-semibold">
          <Icon className={done ? "size-5 text-ok" : "size-5 text-muted-foreground"} aria-hidden />
          {title}
          <span className="sr-only">{done ? "(complete)" : done === null ? "(not needed)" : "(to do)"}</span>
        </h3>
        <span className="text-xs text-muted-foreground">{required}</span>
      </div>
      <div className="space-y-2 sm:pl-7">{children}</div>
      {aside}
    </section>
  );
}

function Feedback({ v, onRevise, revising }: { v: Version; onRevise: () => void; revising: boolean }) {
  if (!["CHANGES_REQUESTED", "REJECTED"].includes(v.review_status) && !(v.state === "DRAFT" && v.requested_items?.length)) return null;
  const draft = v.state === "DRAFT";
  return (
    <section className="rounded-lg border border-warn/40 bg-warn-soft p-5" aria-labelledby="fb-h">
      <h2 id="fb-h" className="text-lg font-semibold">{draft ? "What Northbridge asked you to correct" : v.review_status === "REJECTED" ? v.decision_source === "automatic" ? "Automatic rejection reason" : "Reviewer's reason" : "Northbridge requested changes"}</h2>
      {v.applicant_message && <p className="mt-2">{v.applicant_message}</p>}
      {v.requested_items?.length ? (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
          {v.requested_items.map((it, i) => <li key={i}><strong>{CATEGORY_LABEL[it.category]}:</strong> {it.instruction}</li>)}
        </ul>
      ) : null}
      {v.review_status === "CHANGES_REQUESTED" && !draft && (
        <Button className="mt-4" onClick={onRevise} disabled={revising}>Start a correction</Button>
      )}
    </section>
  );
}

export function ApplicantDashboard() {
  const qc = useQueryClient();
  const me = useMe();
  const app = useApplication();
  const [consent, setConsent] = useState(false);
  const [submitErr, setSubmitErr] = useState<string[] | null>(null);

  const call = useMutation({
    mutationFn: ({ path, body, method }: { path: string; body?: unknown; method?: string }) =>
      api<Application>(path, { method: method ?? "POST", json: body }),
    onSuccess: (data) => { qc.setQueryData(["application"], data); setSubmitErr(null); },
    onError: (e) => {
      if (e instanceof ApiError && e.items) setSubmitErr(e.items);
      else toast.error(e instanceof ApiError ? e.message : "Something went wrong.");
    },
  });

  if (app.isLoading) return <div className="space-y-4"><Skeleton className="h-32" /><Skeleton className="h-96" /></div>;
  if (app.isError || !app.data) {
    return <Alert tone="danger" title="Your application could not be loaded">
      {(app.error as Error)?.message} <Button variant="link" onClick={() => app.refetch()}>Try again</Button>
    </Alert>;
  }
  const a = app.data;
  const v = a.current;
  const pol = a.policy;
  const editable = v.state === "DRAFT";
  const docs = v.documents;
  const by = (c: string) => docs.filter((d) => d.category === c);
  const slips = by("SALARY_SLIP");
  const processing = ["QUEUED", "RUNNING"].includes(v.processing_status);
  const canRevise = v.state === "SUBMITTED" && !processing && v.review_status !== "APPROVED";
  const revise = () => call.mutate({ path: `/v1/applications/${a.id}/revise`, body: { row_version: v.row_version } });

  return (
    <div className="space-y-6">
      {me.data && !me.data.email_verified && (
        <Alert tone="info" title="Confirm your email address">
          We sent you a link. <Button variant="link" size="sm" onClick={() => api("/v1/auth/resend-verification", { method: "POST" }).then(() => toast.success("Link sent"))}>Send it again</Button>
        </Alert>
      )}
      <ApplicantResult version={v} />
      <Feedback v={v} onRevise={revise} revising={call.isPending} />

      <div className="space-y-6">
        <div className="space-y-6">
          <section className="rounded-lg border bg-card p-5" aria-labelledby="docs-h">
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="docs-h" className="text-lg font-semibold">Your documents</h2>
              <span className="text-xs text-muted-foreground">Reference {a.reference} · version {v.version_number}</span>
            </div>
            {!editable && <p className="mb-4 text-sm text-muted-foreground">This version is submitted and can no longer be edited. {canRevise && "To change a document, start a correction — it creates a new version and earlier results stop counting."}</p>}

            <Section title="Aadhaar card" required="Required" done={by("AADHAAR").length > 0}>
              {by("AADHAAR").map((d) => <DocumentRow key={d.id} doc={d} appId={a.id} editable={editable} maxMb={pol.max_file_mb} />)}
              {editable && by("AADHAAR").length === 0 && <DropZone appId={a.id} category="AADHAAR" maxMb={pol.max_file_mb} />}
            </Section>

            <Section title="PAN card" required="Required" done={by("PAN").length > 0}>
              {by("PAN").map((d) => <DocumentRow key={d.id} doc={d} appId={a.id} editable={editable} maxMb={pol.max_file_mb} />)}
              {editable && by("PAN").length === 0 && <DropZone appId={a.id} category="PAN" maxMb={pol.max_file_mb} />}
              <p className="text-xs text-muted-foreground">The name on your PAN card must match your Aadhaar and salary slips.</p>
            </Section>

            <Section title="Salary slips" required={`At least ${pol.required_salary_slips}, up to ${pol.max_salary_slips}`} done={slips.length >= pol.required_salary_slips}>
              {slips.map((d) => <DocumentRow key={d.id} doc={d} appId={a.id} editable={editable} maxMb={pol.max_file_mb} />)}
              {editable && slips.length < pol.max_salary_slips && (
                <DropZone appId={a.id} category="SALARY_SLIP" maxMb={pol.max_file_mb} label={slips.length ? "Add another salary slip" : undefined} />
              )}
              <p className="text-xs text-muted-foreground">{slips.length} of {pol.max_salary_slips} uploaded. Each salary slip must show your name and the same PAN number as your PAN card. Every page of a salary slip PDF is read.</p>
            </Section>

            {pol.accept_bank_statement && (
              <Section title="Bank statement" required="Optional — does not replace a salary slip" done={by("BANK_STATEMENT").length ? true : null}>
                {by("BANK_STATEMENT").map((d) => <DocumentRow key={d.id} doc={d} appId={a.id} editable={editable} maxMb={pol.max_file_mb} />)}
                {editable && by("BANK_STATEMENT").length === 0 && <DropZone appId={a.id} category="BANK_STATEMENT" maxMb={pol.max_file_mb} />}
              </Section>
            )}
          </section>

          {editable && (
            <section className="rounded-lg border bg-card p-5" aria-labelledby="submit-h">
              <h2 id="submit-h" className="text-lg font-semibold">Submit for checking</h2>
              <p className="mt-1 text-sm text-muted-foreground">Your draft is saved automatically. Use Preview next to each uploaded document to check every page before submitting. You can replace or remove files above.</p>
              {submitErr && <Alert tone="warning" title="Before you submit" className="mt-3"><ul className="list-disc pl-4">{submitErr.map((s) => <li key={s}>{s}</li>)}</ul></Alert>}
              <div className="mt-4 flex items-start gap-3 rounded-md bg-secondary p-3">
                <Checkbox id="consent" checked={consent} onCheckedChange={(c) => setConsent(!!c)} />
                <Label htmlFor="consent" className="text-sm font-normal leading-relaxed">
                  I consent to Northbridge Finance processing these documents to check that my name is consistent across them.
                  Text is read on Northbridge's servers; names, identity numbers, dates of birth, addresses and contact details are replaced
                  with placeholders before masked text is sent to Google Gemini for structured extraction. Original files are kept for review
                  and deleted {pol.retention_days_after_decision} days after a decision under Northbridge's current retention setting.
                </Label>
              </div>
              <Button className="mt-4" disabled={!consent || call.isPending}
                onClick={() => call.mutate({ path: `/v1/applications/${a.id}/submit`, body: { consent, row_version: v.row_version } })}>
                Submit documents
              </Button>
            </section>
          )}

          {canRevise && v.review_status !== "CHANGES_REQUESTED" && (
            <Button variant="outline" onClick={revise} disabled={call.isPending}>Start a correction</Button>
          )}
        </div>


      </div>
    </div>
  );
}
