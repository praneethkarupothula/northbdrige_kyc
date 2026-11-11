import { useQuery } from "@tanstack/react-query";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { AuthFrame } from "./forms";

interface Mail { to: string; subject: string; body: string; created_at: string }

function linkify(body: string) {
  return body.split(/(https?:\/\/\S+)/g).map((part, i) =>
    /^https?:\/\//.test(part) ? <a key={i} className="break-all text-stamp underline" href={part.replace(/^https?:\/\/[^/]+/, "")}>{part}</a> : part);
}

export function DevMailboxPage() {
  const q = useQuery({ queryKey: ["dev-mail"], queryFn: () => api<Mail[]>("/v1/dev/emails"), refetchInterval: 4000 });
  return (
    <AuthFrame title="Development mailbox">
      <Alert tone="warning" title="Development only">No email server is configured, so messages are captured here instead of being sent. This page is disabled in production.</Alert>
      <ul className="mt-6 space-y-4">
        {q.data?.map((m, i) => (
          <li key={i} className="rounded-md border bg-card p-4 text-sm">
            <p className="font-medium">{m.subject}</p>
            <p className="text-xs text-muted-foreground">To {m.to} · {formatDate(m.created_at)}</p>
            <p className="mt-2 whitespace-pre-wrap">{linkify(m.body)}</p>
          </li>
        ))}
        {q.data?.length === 0 && <p className="text-muted-foreground">No messages yet.</p>}
        {q.isError && <Alert tone="danger">The development mailbox is unavailable in this environment.</Alert>}
      </ul>
    </AuthFrame>
  );
}
