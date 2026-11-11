import { useQueryClient } from "@tanstack/react-query";
import { FileText, ImageIcon, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/misc";
import { api, ApiError, uploadWithProgress } from "@/lib/api";
import type { Doc } from "@/lib/types";
import { CATEGORY_LABEL, cn, formatBytes } from "@/lib/utils";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

/** Quick client-side check for helpful errors; the server re-validates the actual bytes. */
export function precheck(file: File, maxMb: number): string | null {
  const ok = /\.(pdf|jpe?g|png)$/i.test(file.name);
  if (!ok) return "Choose a PDF, JPG/JPEG or PNG file.";
  if (file.size > maxMb * 1024 * 1024) return `This file is ${formatBytes(file.size)}. The limit is ${maxMb} MB.`;
  if (file.size === 0) return "This file is empty.";
  return null;
}

export function DropZone({ appId, category, maxMb, disabled, replaceId, label, compact = false }: {
  appId: string; category: string; maxMb: number; disabled?: boolean; replaceId?: string; label?: string; compact?: boolean;
}) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [drag, setDrag] = useState(false);
  const [pending, setPending] = useState<{ name: string; pct: number; preview?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => () => { if (pending?.preview) URL.revokeObjectURL(pending.preview); }, [pending?.preview]);

  async function send(file: File) {
    setError(null);
    const problem = precheck(file, maxMb);
    if (problem) return setError(problem);
    const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
    setPending({ name: file.name, pct: 0, preview });
    const form = new FormData();
    form.append("category", category);
    form.append("file", file);
    if (replaceId) form.append("replace_document_id", replaceId);
    try {
      const r = await uploadWithProgress<{ warning: string | null }>(`/v1/applications/${appId}/documents`, form, (pct) =>
        setPending((p) => (p ? { ...p, pct } : p)));
      toast.success(`${CATEGORY_LABEL[category]} uploaded`);
      if (r.warning) toast.warning(r.warning);
      await qc.invalidateQueries({ queryKey: ["application"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setPending(null);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div>
      <label
        htmlFor={id}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f && !disabled) void send(f); }}
        className={cn(
          "flex cursor-pointer items-center gap-3 rounded-md border border-dashed border-input bg-background/60 text-sm transition-colors focus-within:outline-2 focus-within:outline-ring",
          compact ? "px-3 py-2" : "px-4 py-5",
          drag && "border-stamp bg-accent",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        {replaceId ? <RefreshCw className="size-4 text-muted-foreground" aria-hidden /> : <UploadCloud className="size-5 text-muted-foreground" aria-hidden />}
        <span className="flex-1">
          <span className="font-medium">{label ?? (replaceId ? "Replace file" : "Drop a file here or browse")}</span>
          {!compact && <span className="block text-xs text-muted-foreground">PDF, JPG or PNG, up to {maxMb} MB</span>}
        </span>
        <input
          id={id} ref={input} type="file" accept={ACCEPT} className="sr-only" disabled={disabled || !!pending}
          aria-label={`${replaceId ? "Replace" : "Upload"} ${CATEGORY_LABEL[category]}`}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f); }}
        />
      </label>
      {pending && (
        <div className="mt-2 flex items-center gap-3" aria-live="polite">
          {pending.preview ? <img src={pending.preview} alt="" className="size-10 rounded object-cover" /> : <FileText className="size-5" aria-hidden />}
          <div className="flex-1">
            <p className="truncate text-xs">Uploading {pending.name} — {pending.pct}%</p>
            <Progress value={pending.pct} label="Upload progress" />
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-bad">{error}</p>}
    </div>
  );
}

export function DocumentPreview({ doc }: { doc: Doc }) {
  const [page, setPage] = useState(1);
  const [failed, setFailed] = useState(false);
  if (doc.purged) return <span className="text-xs text-muted-foreground">File no longer available</span>;
  const label = CATEGORY_LABEL[doc.category];
  return (
    <Dialog>
      <DialogTrigger asChild><Button variant="outline" size="sm" aria-label={`Preview ${label}`}>Preview</Button></DialogTrigger>
      <DialogContent className="max-w-4xl">
        <DialogTitle>{label} preview</DialogTitle>
        <DialogDescription>Review the file you uploaded. Close this preview to replace or remove it before submitting.</DialogDescription>
        {doc.page_count > 1 && <div className="flex flex-wrap gap-2" role="group" aria-label="Document pages">
          {Array.from({ length: doc.page_count }, (_, i) => i + 1).map((n) => (
            <Button key={n} size="sm" variant={n === page ? "default" : "outline"} aria-pressed={n === page}
              onClick={() => { setPage(n); setFailed(false); }}>Page {n}</Button>
          ))}
        </div>}
        {failed ? <p role="alert">The preview could not be loaded. Try opening the original file below.</p>
          : <img key={`${doc.id}-${page}`} src={`/v1/documents/${doc.id}/pages/${page}?reveal=true`}
            alt={`${label} uploaded document, page ${page}`} onError={() => setFailed(true)}
            className="mx-auto max-h-[65vh] max-w-full rounded border object-contain" />}
        <Button asChild variant="link"><a href={`/v1/documents/${doc.id}/original`} target="_blank" rel="noreferrer">Open original file</a></Button>
      </DialogContent>
    </Dialog>
  );
}

export function DocumentRow({ doc, appId, editable, maxMb, onDeleted }: {
  doc: Doc; appId: string; editable: boolean; maxMb: number; onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const Icon = doc.file_format === "pdf" ? FileText : ImageIcon;
  async function remove() {
    setBusy(true);
    try {
      await api(`/v1/applications/${appId}/documents/${doc.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["application"] });
      onDeleted?.();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not remove the file.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={"rounded-md border bg-card p-3"} data-testid={`doc-${doc.category}`}>
      <div className="flex flex-wrap items-center gap-3">
        <Icon className="size-5 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {doc.file_format.toUpperCase()} · {doc.page_count} page{doc.page_count > 1 ? "s" : ""} · {formatBytes(doc.size_bytes)}
          </p>
          {doc.duplicate_of && <p className="text-xs text-warn">Identical to another uploaded file — it will be counted once.</p>}
        </div>

        <DocumentPreview doc={doc} />
        {editable && (
          <Button variant="ghost" size="icon" onClick={remove} disabled={busy} aria-label={`Remove ${CATEGORY_LABEL[doc.category]}`}>
            <Trash2 />
          </Button>
        )}
      </div>
      {editable && <div className="mt-2"><DropZone appId={appId} category={doc.category} maxMb={maxMb} replaceId={doc.id} compact /></div>}
    </div>
  );
}
