import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusHero } from "@/components/status";
import { ProcessingTimeline } from "@/components/timeline";
import { precheck } from "@/components/uploader";
import { useProgress } from "@/hooks/useProgress";
import type { ProgressEvent, Version } from "@/lib/types";
import { RegisterPage, registerSchema, strength } from "@/pages/public/forms";

const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient()}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>
);

const baseVersion: Version = {
  id: "v1", version_number: 1, is_current: true, state: "SUBMITTED", processing_status: "SUCCEEDED", check_status: "MATCH",
  review_status: "PENDING_REVIEW", pan_status: "UNAVAILABLE", processing_attempt: 1, row_version: 3, submitted_at: null,
  processed_at: null, decided_at: null, applicant_message: null, requested_items: null,
  status: { key: "MATCH_AWAITING_REVIEW", tone: "info", headline: "Documents match — awaiting Northbridge review.", detail: "x" },
  documents: [], checks: [],
};

afterEach(() => vi.restoreAllMocks());

describe("registration validation", () => {
  it("rejects weak and mismatched passwords", () => {
    expect(registerSchema.safeParse({ full_name: "Asha Verma", email: "a@example.com", password: "short", password_confirm: "short" }).success).toBe(false);
    const r = registerSchema.safeParse({ full_name: "Asha Verma", email: "a@example.com", password: "Correct-Horse-42!", password_confirm: "nope" });
    expect(r.success).toBe(false);
    expect(registerSchema.safeParse({ full_name: "Asha Verma", email: "a@example.com", password: "Correct-Horse-42!", password_confirm: "Correct-Horse-42!" }).success).toBe(true);
    expect(strength("abc").label).toBe("Too weak");
    expect(strength("Correct-Horse-Battery-42!").score).toBeGreaterThanOrEqual(3);
  });

  it("shows inline errors and never offers a role field", async () => {
    render(wrap(<RegisterPage />));
    expect(screen.queryByLabelText(/role/i)).toBeNull();
    await userEvent.type(screen.getByLabelText("Password"), "Correct-Horse-42!");
    await userEvent.type(screen.getByLabelText("Confirm password"), "different");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
  });
});

describe("upload precheck", () => {
  it("accepts only PDF/JPG/PNG within the size limit", () => {
    const f = (name: string, size: number) => new File([new Uint8Array(size)], name);
    expect(precheck(f("a.pdf", 10), 10)).toBeNull();
    expect(precheck(f("a.JPEG", 10), 10)).toBeNull();
    expect(precheck(f("a.docx", 10), 10)).toMatch(/PDF, JPG/);
    expect(precheck(f("a.png", 11 * 1024 * 1024), 10)).toMatch(/limit is 10 MB/);
  });
});

describe("status presentation", () => {
  it("automated MATCH is a typed line, not a stamp", () => {
    const { container } = render(wrap(<StatusHero status={baseVersion.status} version={baseVersion} />));
    expect(screen.getByText(/still needs a Northbridge reviewer/)).toBeInTheDocument();
    expect(container.querySelector(".nb-stamp")).toBeNull();
  });
  it("only a reviewer decision renders the stamp", () => {
    const v = { ...baseVersion, review_status: "APPROVED", decided_at: new Date().toISOString(),
      status: { key: "VERIFIED", tone: "success" as const, headline: "Verified — approved by Northbridge.", detail: "" } };
    const { container } = render(wrap(<StatusHero status={v.status} version={v} />));
    expect(container.querySelector(".nb-stamp")).not.toBeNull();
    expect(screen.getByLabelText("Approved by Northbridge")).toBeInTheDocument();
  });
});

describe("processing timeline", () => {
  it("derives stage state from real events only", () => {
    const docs = [{ id: "d1", category: "AADHAAR" }, { id: "d2", category: "SALARY_SLIP" }] as Version["documents"];
    const ev = (id: number, stage: string, status: string, document_id: string | null = null): ProgressEvent =>
      ({ id, version_id: "v1", document_id, attempt: 1, stage, stage_label: stage, status, message: `${stage} ${status}`, created_at: new Date().toISOString() });
    const events = [ev(1, "FILE_VALIDATION", "succeeded", "d1"), ev(2, "FILE_VALIDATION", "succeeded", "d2"),
      ev(3, "TEXT_EXTRACTION", "succeeded", "d1"), ev(4, "TEXT_EXTRACTION", "failed", "d2")];
    const v = { ...baseVersion, processing_status: "RUNNING", check_status: null, review_status: "NOT_READY" };
    const { container } = render(<ProcessingTimeline events={events} version={v} docs={docs} mode="live" />);
    const state = (s: string) => container.querySelector(`[data-stage="${s}"]`)!.getAttribute("data-state");
    expect(state("FILE_VALIDATION")).toBe("done");
    expect(state("TEXT_EXTRACTION")).toBe("failed");
    expect(state("PII_MASKING")).toBe("todo");
    expect(state("DECISION")).toBe("todo");
    expect(screen.getByText("Live updates")).toBeInTheDocument();
  });
});

describe("progress hook", () => {
  it("falls back to polling when EventSource is unavailable", async () => {
    const original = globalThis.EventSource;
    // @ts-expect-error simulate an environment without SSE
    delete globalThis.EventSource;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const after = Number(new URL(String(url), "http://x").searchParams.get("after"));
      const events = after < 7 ? [{ id: 7, version_id: "v", document_id: null, attempt: 1, stage: "UPLOAD_RECEIVED", stage_label: "", status: "info", message: "m", created_at: "" }] : [];
      return new Response(JSON.stringify({ events, status: {} }), { status: 200 });
    });
    const qc = new QueryClient();
    const { result, unmount } = renderHook(() => useProgress("app-1", { pollMs: 20 }), {
      wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>,
    });
    await waitFor(() => expect(result.current.mode).toBe("polling"));
    expect(result.current.events.map((e) => e.id)).toEqual([7]);
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("after=7"))).toBe(true));
    unmount();
    globalThis.EventSource = original;
  });
});

describe("terminal extraction failures", () => {
  it("stops the spinner for older attempts missing a closing extraction event", () => {
    const doc = { id: "d1", category: "AADHAAR", processing_status: "FAILED", current_stage: "SCHEMA_VALIDATION" } as Version["documents"][number];
    const event: ProgressEvent = { id: 1, version_id: "v1", document_id: "d1", attempt: 1,
      stage: "LLM_EXTRACTION", stage_label: "", status: "started", message: "Requesting extraction", created_at: "2026-11-11T00:00:00Z" };
    const { container, unmount } = render(<ProcessingTimeline events={[event]} version={baseVersion} docs={[doc]} mode="live" staff />);
    expect(container.querySelector('[data-stage="LLM_EXTRACTION"]')).toHaveAttribute("data-state", "failed");
    unmount();
  });
});
