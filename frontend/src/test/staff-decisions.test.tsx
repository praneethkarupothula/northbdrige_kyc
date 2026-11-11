import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StaffOverview } from "@/pages/staff/Overview";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { Evidence, DecisionPanel, type StaffApp } from "@/pages/staff/Review";

vi.mock("@/hooks/useAuth", () => ({ useMe: () => ({ data: { role: "reviewer" } }) }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/api")>(), api: vi.fn() }));
const application = { applicant: { full_name: null, is_active: true }, id: "app-1", approval_blockers: [], current: {
  id: "version-1", row_version: 4, review_status: "PENDING_REVIEW", processing_status: "SUCCEEDED", decided_at: null,
} } as unknown as StaffApp;
function show() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><DecisionPanel a={application} /></QueryClientProvider>);
  return userEvent.setup();
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });

describe("staff decision actions", () => {
  it("explains missing request fields, then sends a valid correction request", async () => {
    vi.mocked(api).mockResolvedValue({ detail: "Changes requested" });
    const user = show();
    await user.click(screen.getByRole("button", { name: "Request changes" }));
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 10 characters");
    expect(api).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Message to the applicant"), "Please upload a clearer document.");
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 5 characters");
    await user.type(screen.getByLabelText("What to upload or correct"), "Upload a clear salary slip");
    await user.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/v1/staff/applications/app-1/request-changes", {
      json: { version_id: "version-1", row_version: 4, applicant_message: "Please upload a clearer document.", internal_note: null,
        items: [{ category: "SALARY_SLIP", instruction: "Upload a clear salary slip" }] },
    }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
  it("explains the missing rejection reason message, then rejects", async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.mocked(api).mockResolvedValue({ detail: "Rejected" });
    const user = show();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Reject application" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 10 characters");
    await user.type(screen.getByLabelText("Message to the applicant"), "The names on your documents differ.");
    await user.click(screen.getByRole("button", { name: "Reject application" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/v1/staff/applications/app-1/reject", {
      json: { version_id: "version-1", row_version: 4, applicant_message: "The names on your documents differ.", internal_note: null,
        reason_code: "DOCUMENTS_INCONSISTENT" },
    }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    for (const key of ["dash", "queue", "metrics"]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] });
    }
  });
  it("keeps the dialog open and displays a server error", async () => {
    vi.mocked(api).mockRejectedValue(new ApiError(409, "This application changed. Reload before deciding."));
    const user = show();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.type(screen.getByLabelText("Message to the applicant"), "The names on your documents differ.");
    await user.click(screen.getByRole("button", { name: "Reject application" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Reload before deciding");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});


it("shows the rejected total on the overview and links to rejected applications", async () => {
  vi.mocked(api).mockResolvedValue({ pending_review: 0, mismatches: 0, incomplete: 0, approved: 0,
    rejected: 1, changes_requested: 0, processing_failures: 0, processing: 0, unassigned_pending: 0,
    oldest_pending_age_hours: null, workload: [] });
  const client = new QueryClient();
  render(<QueryClientProvider client={client}><MemoryRouter><StaffOverview /></MemoryRouter></QueryClientProvider>);
  const rejected = await screen.findByRole("link", { name: "1 Rejected" });
  expect(screen.getByRole("link", { name: "0 Mismatches" })).toHaveAttribute("href", "/staff/queue?check_status=MISMATCH");
  expect(screen.getByRole("link", { name: "0 Incomplete" })).toHaveAttribute("href", "/staff/queue?check_status=INCOMPLETE");
  expect(screen.getByRole("link", { name: "0 Processing" })).toHaveAttribute("href", "/staff/queue?processing_status=IN_PROGRESS");
  expect(rejected).toHaveAttribute("href", "/staff/queue?review_status=REJECTED");
});


it("hides irrelevant missing salary fields on Aadhaar", () => {
  const client = new QueryClient();
  const doc = { id: "doc-a", category: "AADHAAR", extraction: { document_type: "AADHAAR",
    fields: { full_name: "A***", father_name: "R***", date_of_birth: null, id_number: "****1234" },
    pay_period: null, gross_salary: null, net_salary: null, currency: null,
    missing_fields: ["employer_name", "gross_salary", "net_salary"], quality_flags: [], extraction_method: "pdf_text", ocr_confidence: null } } as unknown as import("@/lib/types").Doc;
  render(<QueryClientProvider client={client}><Evidence doc={doc} canReveal={false} showExact={false} setHighlight={() => {}} /></QueryClientProvider>);
  expect(screen.getByText("Aadhaar number")).toBeInTheDocument();
  expect(screen.queryByText("Identity number")).toBeNull();
  expect(screen.queryByText("Father’s name (S/O)")).toBeNull();
  expect(screen.queryByText(/Not found on the document/)).toBeNull();
  expect(screen.queryByText("Gross salary")).toBeNull();
});

it("lets an admin cancel approval with a reason and request fresh documents", async () => {
  vi.mocked(api).mockResolvedValue({ detail: "Fresh documents required" });
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const a = { ...application, applicant: { ...application.applicant, full_name: "Test Applicant" },
    current: { ...application.current, review_status: "APPROVED" } } as StaffApp;
  render(<QueryClientProvider client={client}><DecisionPanel a={a} /></QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Cancel approval / require fresh documents" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(api).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText("Reason shown to the applicant"), "Please refresh your expired documents.");
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/v1/staff/applications/app-1/require-reverification", {
    json: { version_id: "version-1", row_version: 4, applicant_message: "Please refresh your expired documents." },
  }));
});
