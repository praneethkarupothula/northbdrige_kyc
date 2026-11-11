import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

const tones = {
  info: { cls: "bg-info-soft border-primary/15", Icon: Info },
  success: { cls: "bg-ok-soft border-ok/25 text-ok", Icon: CheckCircle2 },
  warning: { cls: "bg-warn-soft border-warn/25", Icon: AlertTriangle },
  danger: { cls: "bg-bad-soft border-bad/25", Icon: XCircle },
  neutral: { cls: "bg-muted", Icon: Info },
} as const;

export function Alert({ tone = "info", title, children, className }: {
  tone?: keyof typeof tones; title?: React.ReactNode; children?: React.ReactNode; className?: string;
}) {
  const { cls, Icon } = tones[tone];
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("flex gap-3 rounded-md border p-3 text-sm", cls, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="space-y-1 text-foreground">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="text-foreground/85">{children}</div>}
      </div>
    </div>
  );
}
