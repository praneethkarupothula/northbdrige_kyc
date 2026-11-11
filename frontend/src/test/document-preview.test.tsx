import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { DocumentPreview } from "@/components/uploader";
import type { Doc } from "@/lib/types";

const doc = { id: "draft-file", category: "AADHAAR", page_count: 2, purged: false } as Doc;
afterEach(cleanup);

it("lets an applicant review all uploaded pages and open the original before submission", async () => {
  const user = userEvent.setup();
  render(<DocumentPreview doc={doc} />);
  await user.click(screen.getByRole("button", { name: "Preview Aadhaar card" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAttribute("src", "/v1/documents/draft-file/pages/1?reveal=true");
  await user.click(screen.getByRole("button", { name: "Page 2" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", "/v1/documents/draft-file/pages/2?reveal=true");
  expect(screen.getByRole("link", { name: "Open original file" })).toHaveAttribute("href", "/v1/documents/draft-file/original");
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByRole("alert")).toHaveTextContent("preview could not be loaded");
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("does not offer a preview for a deleted file", () => {
  render(<DocumentPreview doc={{ ...doc, purged: true }} />);
  expect(screen.queryByRole("button")).toBeNull();
});
