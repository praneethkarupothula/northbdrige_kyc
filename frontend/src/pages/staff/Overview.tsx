import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/misc";
import { useMe } from "@/hooks/useAuth";
import { api } from "@/lib/api";
import { ageLabel } from "@/lib/utils";

interface Dash {
  pending_review: number; mismatches: number; incomplete: number; approved: number; rejected: number; changes_requested: number;
  processing_failures: number; processing: number; unassigned_pending: number; oldest_pending_age_hours: number | null;
  workload: { reviewer: string; assigned_pending: number }[];
}

function Count({ n, label, to, tone }: { n: number; label: string; to: string; tone?: string }) {
  return (
    <Link to={to} className="group rounded-lg border bg-card p-4 hover:border-primary/40">
      <p className={`font-serif text-3xl font-semibold tabular-nums ${tone ?? ""}`}>{n}</p>
      <p className="text-sm text-muted-foreground group-hover:text-foreground">{label}</p>
    </Link>
  );
}

export function StaffOverview() {
  const me = useMe();
  const d = useQuery({ queryKey: ["dash"], queryFn: () => api<Dash>("/v1/staff/dashboard"), refetchInterval: 5000 });
  const m = useQuery({ queryKey: ["metrics"], enabled: me.data?.role === "admin", queryFn: () => api<Record<string, unknown>>("/v1/admin/metrics"), refetchInterval: 5000 });
  if (d.isLoading) return <Skeleton className="h-64" />;
  if (!d.data) return <Alert tone="danger">The overview could not be loaded.</Alert>;
  const x = d.data;
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-semibold">Overview</h1>
        <p className="text-muted-foreground">Oldest application waiting for review: {ageLabel(x.oldest_pending_age_hours)} · {x.unassigned_pending} unassigned</p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Count n={x.processing} label="Processing" to="/staff/queue?processing_status=IN_PROGRESS" />
        <Count n={x.unassigned_pending} label="Unassigned pending" to="/staff/queue?review_status=PENDING_REVIEW&assignment=unassigned" />
        <Count n={x.pending_review} label="Pending review" to="/staff/queue?review_status=PENDING_REVIEW" />
        <Count n={x.mismatches} label="Mismatches" to="/staff/queue?check_status=MISMATCH" tone="text-bad" />
        <Count n={x.incomplete} label="Incomplete" to="/staff/queue?check_status=INCOMPLETE" tone="text-warn" />
        <Count n={x.processing_failures} label="Processing failures" to="/staff/queue?processing_status=FAILED" tone="text-bad" />
        <Count n={x.rejected} label="Rejected" to="/staff/queue?review_status=REJECTED" tone="text-bad" />
        <Count n={x.approved} label="Approved" to="/staff/queue?review_status=APPROVED" tone="text-stamp" />
        <Count n={x.changes_requested} label="Changes requested" to="/staff/queue?review_status=CHANGES_REQUESTED" />
      </div>
      <section>
        <h2 className="mb-3 text-lg font-semibold">Reviewer workload</h2>
        {x.workload.length ? (
          <ul className="max-w-md divide-y rounded-lg border bg-card">
            {x.workload.map((w) => <li key={w.reviewer} className="flex justify-between px-4 py-2 text-sm"><span>{w.reviewer}</span><span className="tabular-nums">{w.assigned_pending} assigned</span></li>)}
          </ul>
        ) : <p className="text-sm text-muted-foreground">No pending reviews are assigned yet. Claim one from the review queue.</p>}
      </section>
      {m.data && (
        <section>
          <h2 className="mb-3 text-lg font-semibold">Operational metrics</h2>
          <pre className="overflow-x-auto rounded-lg border bg-card p-4 text-xs">{JSON.stringify(m.data, null, 2)}</pre>
          <p className="mt-2 text-xs text-muted-foreground">Metadata only — counts, statuses and error classes. No personal data.</p>
        </section>
      )}
    </div>
  );
}
