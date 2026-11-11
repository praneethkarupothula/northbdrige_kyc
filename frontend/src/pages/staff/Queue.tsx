import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { StatusBadge } from "@/components/status";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { ageLabel, CATEGORY_LABEL, formatDate } from "@/lib/utils";

interface Row {
  application_id: string; reference: string; version_number: number; applicant: string; state: string; processing_status: string;
  check_status: string | null; review_status: string; submitted_at: string | null; age_hours: number | null;
  assigned_reviewer: string | null; assigned_to_me: boolean; document_types: string[];
}

const FILTERS: { key: string; label: string; options: [string, string][] }[] = [
  { key: "review_status", label: "Review", options: [["PENDING_REVIEW", "Pending review"], ["APPROVED", "Approved"], ["REJECTED", "Rejected"], ["CHANGES_REQUESTED", "Changes requested"], ["NOT_READY", "Not ready"]] },
  { key: "check_status", label: "Automated check", options: [["MATCH", "Match"], ["MISMATCH", "Mismatch"], ["INCOMPLETE", "Incomplete"]] },
  { key: "processing_status", label: "Processing", options: [["IN_PROGRESS", "In progress"], ["QUEUED", "Queued"], ["RUNNING", "Running"], ["SUCCEEDED", "Processed"], ["FAILED", "Failed"], ["NEEDS_REUPLOAD", "Needs re-upload"]] },
  { key: "document_type", label: "Has document", options: [["AADHAAR", "Aadhaar"], ["PAN", "PAN"], ["SALARY_SLIP", "Salary slip"], ["BANK_STATEMENT", "Bank statement"]] },
  { key: "assignment", label: "Assignment", options: [["me", "Assigned to me"], ["unassigned", "Unassigned"]] },
];

export function QueuePage() {
  const [sp, setSp] = useSearchParams();
  const page = Number(sp.get("page") ?? 1);
  const qs = sp.toString();
  const q = useQuery({ queryKey: ["queue", qs], queryFn: () => api<{ total: number; items: Row[]; page_size: number }>(`/v1/staff/applications?${qs}`), refetchInterval: 5000 });
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setSp(n); };
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / q.data.page_size)) : 1;
  return (
    <div className="space-y-5">
      <h1 className="text-3xl font-semibold">Review queue</h1>
      <form className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8" onSubmit={(e) => e.preventDefault()} aria-label="Filters">
        <div className="col-span-2 space-y-1"><Label htmlFor="q">Reference or exact email</Label>
          <Input id="q" defaultValue={sp.get("q") ?? ""} onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value)} placeholder="NB-…" />
        </div>
        {FILTERS.map((f) => (
          <div key={f.key} className="space-y-1">
            <Label htmlFor={f.key}>{f.label}</Label>
            <NativeSelect id={f.key} value={sp.get(f.key) ?? ""} onChange={(e) => set(f.key, e.target.value)}>
              <option value="">Any</option>
              {f.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </NativeSelect>
          </div>
        ))}
        <div className="space-y-1"><Label htmlFor="from">Submitted from</Label><Input id="from" type="date" value={sp.get("submitted_from") ?? ""} onChange={(e) => set("submitted_from", e.target.value)} /></div>
      </form>
      {q.isLoading ? <Skeleton className="h-64" /> : q.isError ? <Alert tone="danger">The queue could not be loaded.</Alert> : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="border-b text-left text-muted-foreground">
              <tr>{["Reference", "Applicant", "Documents", "Processing", "Automated check", "Review", "Waiting", "Reviewer"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y">
              {q.data!.items.map((r) => (
                <tr key={r.application_id} className="hover:bg-secondary/50">
                  <td className="px-3 py-2"><Link className="font-medium text-stamp underline-offset-4 hover:underline" to={`/staff/applications/${r.application_id}`}>{r.reference}</Link><span className="block text-xs text-muted-foreground">v{r.version_number} · {formatDate(r.submitted_at)}</span></td>
                  <td className="px-3 py-2">{r.applicant}</td>
                  <td className="px-3 py-2 text-xs">{r.document_types.map((t) => CATEGORY_LABEL[t]).join(", ")}</td>
                  <td className="px-3 py-2"><StatusBadge value={r.processing_status} /></td>
                  <td className="px-3 py-2"><StatusBadge value={r.check_status} /></td>
                  <td className="px-3 py-2"><StatusBadge value={r.review_status} /></td>
                  <td className={`px-3 py-2 tabular-nums ${(r.age_hours ?? 0) > 48 ? "text-bad" : ""}`}>{ageLabel(r.age_hours)}</td>
                  <td className="px-3 py-2">{r.assigned_to_me ? "You" : r.assigned_reviewer ?? "—"}</td>
                </tr>
              ))}
              {!q.data!.items.length && <tr><td colSpan={8} className="px-3 py-10 text-center text-muted-foreground">No applications match these filters.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{q.data?.total ?? 0} applications</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>Previous</Button>
          <span>Page {page} of {pages}</span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>Next</Button>
        </div>
      </div>
    </div>
  );
}
