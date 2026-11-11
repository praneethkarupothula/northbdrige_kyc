import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { StatusBadge } from "@/components/status";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FieldError, Skeleton } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import type { Policy } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const err = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Action failed");

interface Staff { id: string; full_name: string; email: string; role: string; is_active: boolean; has_password: boolean; created_at: string }
const staffSchema = z.object({ full_name: z.string().min(2, "Enter a name."), email: z.string().email("Enter a valid email."), role: z.enum(["reviewer", "admin"]) });

export function StaffAdminPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["staff"], queryFn: () => api<Staff[]>("/v1/admin/staff") });
  const f = useForm<z.infer<typeof staffSchema>>({ resolver: zodResolver(staffSchema), defaultValues: { role: "reviewer" } });
  const update = useMutation({ mutationFn: ({ id, body }: { id: string; body: unknown }) => api(`/v1/admin/staff/${id}`, { method: "PATCH", json: body }), onSuccess: () => qc.invalidateQueries({ queryKey: ["staff"] }), onError: err });
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold">Staff</h1>
      <Card>
        <CardHeader><CardTitle>Invite a staff member</CardTitle><CardDescription>They receive a single-use link to set their own password. No passwords are created or shared by administrators.</CardDescription></CardHeader>
        <CardContent>
          <form className="grid gap-3 md:grid-cols-[1fr_1fr_160px_auto] md:items-end" noValidate onSubmit={f.handleSubmit(async (v) => {
            try { await api("/v1/admin/staff", { json: v }); toast.success("Invitation sent"); f.reset({ role: "reviewer", full_name: "", email: "" }); qc.invalidateQueries({ queryKey: ["staff"] }); } catch (e) { err(e); }
          })}>
            <div className="space-y-1.5"><Label htmlFor="sn">Full name</Label><Input id="sn" {...f.register("full_name")} /><FieldError>{f.formState.errors.full_name?.message}</FieldError></div>
            <div className="space-y-1.5"><Label htmlFor="se">Email</Label><Input id="se" type="email" {...f.register("email")} /><FieldError>{f.formState.errors.email?.message}</FieldError></div>
            <div className="space-y-1.5"><Label htmlFor="sr">Role</Label><NativeSelect id="sr" {...f.register("role")}><option value="reviewer">Reviewer</option><option value="admin">Administrator</option></NativeSelect></div>
            <Button type="submit">Send invitation</Button>
          </form>
        </CardContent>
      </Card>
      {q.isLoading ? <Skeleton className="h-40" /> : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="border-b text-left text-muted-foreground"><tr>{["Name", "Email", "Role", "Status", ""].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y">
              {q.data?.map((s) => (
                <tr key={s.id}>
                  <td className="px-3 py-2">{s.full_name}</td><td className="px-3 py-2">{s.email}</td>
                  <td className="px-3 py-2"><NativeSelect aria-label={`Role for ${s.full_name}`} className="h-8 w-36" value={s.role} onChange={(e) => update.mutate({ id: s.id, body: { role: e.target.value } })}><option value="reviewer">Reviewer</option><option value="admin">Administrator</option></NativeSelect></td>
                  <td className="px-3 py-2">{!s.is_active ? "Deactivated" : s.has_password ? "Active" : "Invitation pending"}</td>
                  <td className="px-3 py-2 text-right"><Button size="sm" variant="outline" onClick={() => update.mutate({ id: s.id, body: { is_active: !s.is_active } })}>{s.is_active ? "Deactivate" : "Reactivate"}</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function PolicyPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["policy"], queryFn: () => api<Policy & { notice: string }>("/v1/admin/policy") });
  const f = useForm<Policy>({ values: q.data });
  if (!q.data) return <Skeleton className="h-64" />;
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-3xl font-semibold">Document policy</h1>
      <Alert tone="info">These are configurable prototype product settings, not legal determinations. Changes apply to drafts and future submissions.</Alert>
      <form className="space-y-5 rounded-lg border bg-card p-5" onSubmit={f.handleSubmit(async (v) => {
        try {
          await api("/v1/admin/policy", { method: "PUT", json: { ...v, max_salary_slips: Number(v.max_salary_slips), required_salary_slips: Number(v.required_salary_slips), max_file_mb: Number(v.max_file_mb), retention_days_after_decision: Number(v.retention_days_after_decision), accept_bank_statement: String(v.accept_bank_statement) === "true" } });
          toast.success("Policy saved"); qc.invalidateQueries({ queryKey: ["policy"] });
        } catch (e) { err(e); }
      })}>
        <div className="space-y-1.5"><Label htmlFor="pp">PAN card</Label>
          <NativeSelect id="pp" {...f.register("pan_policy")}><option value="required">Required — names and salary-slip PAN must match</option></NativeSelect>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5"><Label htmlFor="req">Salary slips required</Label><Input id="req" type="number" min={1} max={12} {...f.register("required_salary_slips")} /></div>
          <div className="space-y-1.5"><Label htmlFor="max">Maximum salary slips</Label><Input id="max" type="number" min={1} max={12} {...f.register("max_salary_slips")} /></div>
          <div className="space-y-1.5"><Label htmlFor="mb">Maximum file size (MB, up to 10)</Label><Input id="mb" type="number" min={1} max={10} {...f.register("max_file_mb")} /></div>
          <div className="space-y-1.5"><Label htmlFor="ret">Delete original files this many days after a decision</Label><Input id="ret" type="number" min={0} {...f.register("retention_days_after_decision")} /></div>
        </div>
        <div className="space-y-1.5"><Label htmlFor="bank">Bank statements</Label><NativeSelect id="bank" {...f.register("accept_bank_statement")}><option value="true">Accepted (informational, first page read)</option><option value="false">Not accepted</option></NativeSelect></div>
        <Button type="submit">Save policy</Button>
      </form>
    </div>
  );
}

interface Group { group_ref: string; id_type: string; masked_value: string; names_agree_across_claims: boolean; claims: { claim_id: string; status: string; application_reference: string; application_id: string; created_at: string; applicant_masked: string }[] }

export function ConflictsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["conflicts"], queryFn: () => api<Group[]>("/v1/admin/identity-conflicts") });
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [note, setNote] = useState<Record<string, string>>({});
  const resolve = useMutation({
    mutationFn: (g: Group) => api("/v1/admin/identity-conflicts/resolve", { json: { fingerprint_ref: g.group_ref, holder_claim_id: choice[g.group_ref] === "none" ? null : choice[g.group_ref], note: note[g.group_ref] } }),
    onSuccess: () => { toast.success("Resolved; affected applications were re-queued"); qc.invalidateQueries({ queryKey: ["conflicts"] }); }, onError: err,
  });
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-3xl font-semibold">Identity conflicts</h1>
      <p className="text-muted-foreground">When more than one account submits the same Aadhaar or PAN number, no one is blocked automatically. Decide which account holds the identifier after verifying it outside this system. Every resolution is audited.</p>
      {q.isLoading ? <Skeleton className="h-40" /> : !q.data?.length ? <Alert tone="success">No open identity conflicts.</Alert> : q.data.map((g) => (
        <Card key={g.group_ref}>
          <CardHeader><CardTitle>{g.id_type} {g.masked_value}</CardTitle><CardDescription>Extracted names {g.names_agree_across_claims ? "agree" : "differ"} across the {g.claims.length} claims.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <fieldset className="space-y-2">
              <legend className="sr-only">Holder</legend>
              {g.claims.map((c) => (
                <label key={c.claim_id} className="flex items-center gap-3 rounded-md border p-2 text-sm">
                  <input type="radio" name={g.group_ref} value={c.claim_id} onChange={() => setChoice({ ...choice, [g.group_ref]: c.claim_id })} />
                  <span className="flex-1">{c.application_reference} — {c.applicant_masked} <span className="text-xs text-muted-foreground">claimed {formatDate(c.created_at)}</span></span>
                  <StatusBadge value={c.status === "CONFLICT" ? "MISMATCH" : c.status} />
                </label>
              ))}
              <label className="flex items-center gap-3 rounded-md border p-2 text-sm"><input type="radio" name={g.group_ref} value="none" onChange={() => setChoice({ ...choice, [g.group_ref]: "none" })} />None of these accounts holds this identifier</label>
            </fieldset>
            <Label htmlFor={`n-${g.group_ref}`}>Resolution note (how it was verified)</Label>
            <Textarea id={`n-${g.group_ref}`} value={note[g.group_ref] ?? ""} onChange={(e) => setNote({ ...note, [g.group_ref]: e.target.value })} />
            <Button disabled={!choice[g.group_ref] || (note[g.group_ref] ?? "").length < 5} onClick={() => resolve.mutate(g)}>Record resolution</Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
