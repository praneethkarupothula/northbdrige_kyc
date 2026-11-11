import { execSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

/**
 * Primary acceptance journey (synthetic data, scripted LLM test double, real PostgreSQL + worker):
 * register -> upload Aadhaar, PAN, two salary slips -> watch real processing -> automated MATCH
 * -> reviewer examines evidence and approves -> applicant sees the verified status.
 */
import { E2E_ENV, ROOT } from "./env";

// Cross-platform (Windows PowerShell, macOS, Linux): run backend commands with the E2E environment.
const backend = (args: string, extra: Record<string, string> = {}) =>
  execSync(`uv run ${args}`, { cwd: path.join(ROOT, "backend"), encoding: "utf8", env: { ...process.env, ...E2E_ENV, ...extra } });
const PASSWORD = "E2e-Strong-Passw0rd!";

test("applicant to reviewer approval journey", async ({ browser }) => {
  const run = Date.now();
  const fxDir = path.join(ROOT, "tests", "e2e", ".fixtures", String(run));
  const fx = JSON.parse(backend(`python ../scripts/make_e2e_fixtures.py ${run % 100000} "${fxDir}"`).trim().split("\n").pop()!);
  const adminEmail = `admin-${run}@northbridge.example.com`;
  backend(`python -m app.cli bootstrap-admin --email ${adminEmail} --name "E2E Reviewer"`, { NB_BOOTSTRAP_PASSWORD: PASSWORD });

  // ---- applicant ----
  const applicant = await browser.newPage();
  await applicant.goto("/");
  await applicant.getByRole("link", { name: "Create an account" }).click();
  await applicant.getByLabel("Full name").fill(fx.name);
  await applicant.getByLabel("Email").fill(`applicant-${run}@example.com`);
  await applicant.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await applicant.getByLabel("Confirm password").fill(PASSWORD);
  await applicant.getByRole("button", { name: "Create account" }).click();
  await expect(applicant.getByRole("heading", { name: "Draft — not submitted yet" })).toBeVisible();

  const up = async (label: string, file: string) => {
    await applicant.getByLabel(label).first().setInputFiles(path.join(fx.dir, file));
    await expect(applicant.getByText(/uploaded/).first()).toBeVisible();
  };
  await up("Upload Aadhaar card", "aadhaar.pdf");
  await expect(applicant.getByTestId("doc-AADHAAR")).toBeVisible();
  await up("Upload PAN card", "pan.pdf");
  await expect(applicant.getByTestId("doc-PAN")).toBeVisible();
  await up("Upload Salary slip", "salary_march.pdf");
  await up("Upload Salary slip", "salary_april.pdf");
  await expect(applicant.getByTestId("doc-SALARY_SLIP")).toHaveCount(2);

  await applicant.getByLabel(/I consent/).check();
  await applicant.getByRole("button", { name: "Submit documents" }).click();

  // real backend progress, persisted events
  await expect(applicant.locator('[data-stage="NAME_CHECKS"][data-state="done"]')).toBeVisible({ timeout: 90_000 });
  await expect(applicant.getByRole("heading", { name: "Documents match — awaiting Northbridge review." })).toBeVisible();
  await expect(applicant.getByTestId("conn-mode")).toContainText(/Live updates|Updating/);
  await applicant.reload();  // refresh keeps state
  await expect(applicant.locator('[data-stage="PII_MASKING"][data-state="done"]')).toBeVisible();
  await expect(applicant.locator(".nb-stamp")).toHaveCount(0);  // no stamp before a human decision

  // ---- reviewer ----
  const staffCtx = await browser.newContext();
  const staff = await staffCtx.newPage();
  await staff.goto("/staff/login");
  await staff.getByLabel("Email").fill(adminEmail);
  await staff.getByLabel("Password").fill(PASSWORD);
  await staff.getByRole("button", { name: "Sign in" }).click();
  await expect(staff.getByRole("heading", { name: "Overview" })).toBeVisible();
  await staff.getByRole("link", { name: "Review queue" }).click();
  const ref = (await applicant.getByText(/Reference NB-/).textContent())!.match(/NB-[0-9A-F]+/)![0];
  await staff.getByLabel("Reference or exact email").fill(ref);
  await staff.getByLabel("Reference or exact email").press("Enter");
  await staff.getByRole("link", { name: ref }).click();
  await expect(staff.getByRole("heading", { name: ref })).toBeVisible();
  await expect(staff.getByAltText(/sensitive areas masked/)).toBeVisible();
  await staff.getByRole("tab", { name: "Name comparison" }).click();
  await expect(staff.getByText("The names are identical after normalising case, spacing and punctuation.").first()).toBeVisible();
  await staff.getByRole("button", { name: "Claim review" }).click();
  await expect(staff.getByRole("button", { name: "Release" })).toBeVisible();
  await staff.getByRole("button", { name: "Approve" }).click();
  await expect(staff.locator(".nb-stamp")).toBeVisible();

  // ---- applicant sees the human decision live ----
  await expect(applicant.getByRole("heading", { name: "Verified — approved by Northbridge." })).toBeVisible({ timeout: 30_000 });
  await expect(applicant.locator(".nb-stamp")).toBeVisible();
  await expect(applicant.getByTestId("notif-badge")).toBeVisible();
});

test("applicant cannot open the staff portal", async ({ page }) => {
  await page.goto("/staff");
  await expect(page).toHaveURL(/\/staff\/login/);
});
