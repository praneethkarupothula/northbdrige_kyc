import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Notification } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export function NotificationBell() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ unread: number; items: Notification[] }>("/v1/notifications"),
    refetchInterval: 5000,
  });
  const readAll = useMutation({
    mutationFn: () => api("/v1/notifications/read-all", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const unread = q.data?.unread ?? 0;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={`Notifications, ${unread} unread`}>
          <Bell />
          {unread > 0 && (
            <span data-testid="notif-badge" className="absolute -right-0.5 -top-0.5 flex min-w-5 items-center justify-center rounded-full bg-stamp px-1 text-[11px] font-semibold text-white">
              {unread}
            </span>
          )}
        </Button>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content align="end" sideOffset={6} className="z-50 w-[min(92vw,380px)] rounded-lg border bg-card p-2 shadow-lg">
          <div className="flex items-center justify-between px-2 py-1">
            <p className="font-medium">Notifications</p>
            {unread > 0 && <Button variant="link" size="sm" onClick={() => readAll.mutate()}>Mark all as read</Button>}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {q.data?.items.length ? q.data.items.map((n) => (
              <Dropdown.Item key={n.id} className="rounded-md px-2 py-2 outline-none data-[highlighted]:bg-secondary">
                <p className={`text-sm ${n.read ? "" : "font-semibold"}`}>{n.title}</p>
                <p className="text-xs text-muted-foreground line-clamp-3">{n.body}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">{formatDate(n.created_at)}</p>
              </Dropdown.Item>
            )) : <p className="px-2 py-6 text-center text-sm text-muted-foreground">No notifications yet.</p>}
          </div>
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
