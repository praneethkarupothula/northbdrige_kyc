import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApplicantResult } from "@/components/applicant-result";
import { ApplicantDashboard } from "@/pages/applicant/Dashboard";
import type { Application, Doc, Version } from "@/lib/types";

vi.mock("@/hooks/useAuth", () => ({ useMe: () => ({ data: { email_verified: true } }) }));

const doc: Doc = {
  id: "salary", category: "SALARY_SLIP", file_format: "pdf", size_bytes: 500, page_count: 1,
  pages_processed: [1], processing_status: "RUNNING", current_stage: "LLM_EXTRACTION",
  error_code: null, error_message: null, quality_flags: [], quality_suggestions: ["Internal scan suggestion"],
  uploaded_at: "2026-11-11T00:00:00Z", purged: false, duplicate_of: null, extraction: null, attention: "Internal attention",
};
const version: Version = {
  id: "v1", version_number: 1, is_current: true, state: "SUBMITTED", processing_status: "SUCCEEDED",
  check_status: "MATCH", review_status: "PENDING_REVIEW", pan_status: "UNAVAILABLE", processing_attempt: 1,
  row_version: 1, submitted_at: null, processed_at: null, decided_at: null, applicant_message: null,
  requested_items: null, status: { key: "PROCESSING", tone: "info", headline: "Internal status", detail: "Internal detail" },
  documents: [doc], checks: [{ id: "c1", check_type: "NAME_SALARY_VS_AADHAAR", status: "MISMATCH",
    code: "NAMES_DIFFER", document_id: "salary", reference_document_id: "aadhaar", detail: {},
    explanation: "The name on the salary slip differs from Aadhaar." }],
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("applicant results", () => {
  it("shows completed KYC only after human approval", () => {
    render(<ApplicantResult version={{ ...version, review_status: "APPROVED", decided_at: "2026-11-11T10:00:00Z" }} />);
    expect(screen.getByRole("heading", { name: "KYC completed and approved" })).toBeInTheDocument();
    expect(screen.getByText(/No further action is needed/)).toBeInTheDocument();
    expect(screen.getByText(/Approved on/)).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByLabelText("Approved by Northbridge")).toHaveClass("nb-stamp");
    expect(screen.queryByText("Your documents match")).toBeNull();
  });
  it("shows the staff rejection stamp even when automated names matched", () => {
    render(<ApplicantResult version={{ ...version, review_status: "REJECTED", decided_at: "2026-11-11T10:00:00Z" }} />);
    expect(screen.getByRole("heading", { name: "KYC application rejected" })).toBeInTheDocument();
    expect(screen.getByLabelText("Not approved by Northbridge")).toHaveClass("nb-stamp");
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.queryByText("Your documents match")).toBeNull();
  });
  it("shows the ownership conflict and caution even if an earlier name result matched", () => {
    render(<ApplicantResult version={{ ...version, status: { key: "IDENTITY_CONFLICT", tone: "warning",
      headline: "Review required", detail: "This identity is already associated with an account. Please sign in or contact support." } }} />);
    expect(screen.getByRole("heading", { name: "Identity document conflict - staff review required" })).toBeInTheDocument();
    expect(screen.getByText(/sign in or contact support/)).toBeInTheDocument();
    expect(screen.getByText(/forged or altered/)).toBeInTheDocument();
    expect(screen.queryByText("Your documents match")).toBeNull();
  });
  it("identifies an automatic duplicate rejection rather than a human review", () => {
    render(<ApplicantResult version={{ ...version, review_status: "REJECTED", decision_source: "automatic" }} />);
    expect(screen.getByText(/automatically rejected because/)).toBeInTheDocument();
    expect(screen.getByText(/Automatic document check/)).toBeInTheDocument();
    expect(screen.queryByText("Your documents match")).toBeNull();
  });
  it("shows a match without internal review or processing details", () => {
    render(<ApplicantResult version={version} />);
    expect(screen.getByRole("heading", { name: "Your documents match" })).toBeInTheDocument();
    expect(screen.queryByText(/differs from Aadhaar/)).toBeNull();
    expect(screen.queryByText(/Internal status/)).toBeNull();
    expect(screen.queryByText("KYC completed and approved")).toBeNull();
    expect(screen.queryByLabelText("Approved by Northbridge")).toBeNull();
  });
  it("explains a mismatch and identifies the affected document", () => {
    render(<ApplicantResult version={{ ...version, check_status: "MISMATCH" }} />);
    expect(screen.getByRole("heading", { name: "Your documents do not match" })).toBeInTheDocument();
    expect(screen.getByText("Salary slip:")).toBeInTheDocument();
    expect(screen.getByText(/differs from Aadhaar/)).toBeInTheDocument();
  });
  it("does not show stale results during reprocessing", () => {
    render(<ApplicantResult version={{ ...version, processing_status: "RUNNING", check_status: "MISMATCH" }} />);
    expect(screen.getByText("Your result will appear here when it is ready.")).toBeInTheDocument();
    expect(screen.queryByText(/differs from Aadhaar/)).toBeNull();
  });
  it("does not call an unreadable document a mismatch", () => {
    render(<ApplicantResult version={{ ...version, processing_status: "FAILED", check_status: null, checks: [] }} />);
    expect(screen.getByRole("heading", { name: "A match could not be confirmed" })).toBeInTheDocument();
    expect(screen.queryByText("Your documents do not match")).toBeNull();
  });
  it("keeps technical processing information out of the applicant dashboard", async () => {
    const app: Application = { id: "a1", reference: "NB-1", created_at: "", current: { ...version, processing_status: "RUNNING", check_status: null }, history: [],
      policy: { pan_policy: "not_requested", max_salary_slips: 6, required_salary_slips: 1, accept_bank_statement: false, max_file_mb: 10, retention_days_after_decision: 7 } };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(app), { status: 200 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(<QueryClientProvider client={client}><ApplicantDashboard /></QueryClientProvider>);
    expect(await screen.findByText("Your result will appear here when it is ready.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your documents" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /PAN card/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("I do not have a PAN card")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Processing" })).toBeNull();
    expect(screen.queryByText(/Pages read|Internal attention|Internal scan suggestion/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel processing" })).toBeNull();
    unmount();
    client.clear();
  });
});
