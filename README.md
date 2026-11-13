# Northbridge KYC

A full-stack applicant onboarding and document-consistency review application. Applicants upload Aadhaar, PAN and salary slips; the system extracts masked evidence, compares names and PAN numbers, and sends the application to staff for a human decision.

> **Scope:** A successful result means the submitted names and PAN numbers agree. This application does not authenticate documents against UIDAI or Income Tax Department records, prove identity, or make loan/credit decisions. Synthetic identifiers can be used for testing; their validity warnings are informational and they are not registered as verified identities.

## Contents 

- [Features](#features)
- [Matching rules](#matching-rules)
- [Duplicate documents and approved accounts](#duplicate-documents-and-approved-accounts)
- [Admin approval cancellation and account closure](#admin-approval-cancellation-and-account-closure)
- [Document extraction and privacy](#document-extraction-and-privacy)
- [Architecture and models](#architecture-and-models)
- [Prerequisites and dependencies](#prerequisites-and-dependencies)
- [Local setup](#local-setup)
- [Run in three terminals](#run-in-three-terminals)
- [Reviewer walkthrough](#reviewer-walkthrough)
- [Tests](#tests)
- [Docker alternative](#docker-alternative)
- [Troubleshooting](#troubleshooting)
- [GitHub preparation](#github-preparation)
- [Project layout and documentation](#project-layout-and-documentation)

## Features

### Applicant portal

- Account registration, sign-in, email verification and password-reset flows.
- Mandatory Aadhaar and PAN uploads, plus at least one salary slip; optional bank statement.
- PDF, JPG/JPEG and PNG uploads with server-side validation and configurable limits.
- Preview every uploaded page and open the original file before submission; replace/remove draft files.
- Consent before processing, saved drafts, corrections and versioned resubmissions.
- Simple Match, Mismatch or Incomplete results with document-specific explanations.
- Approval/rejection stamps, a completed-KYC banner after staff approval, and in-app notifications.
- Local-time display of timestamps. Dashboard and notification polling normally refresh every five seconds while open.

### Staff and administrator portal

- Overview counts and linked queue filters for pending, mismatched, incomplete, processing, approved, rejected and changes-requested applications.
- Full applicant name/email for authorized administrators; masked identity details for other staff by default.
- Uploaded-document inventory, per-document processing status, live timeline, page viewer and original-file access.
- Pydantic OCR schema validation immediately after extraction, with an expandable schema definition in the staff workflow. Gemini call telemetry shows model, latency and token usage after the model response, before evidence validation; missing provider usage is labelled unavailable.
- Extracted fields with evidence boxes, name comparisons, salary-slip PAN comparisons and identity warnings.
- Reviewer assignment, internal notes, reprocessing, human approval, rejection and correction requests.
- Staff management, document policy settings, operational metrics, conflict resolution and audited access.
- PAN is always required under the current product rules; administrators cannot make it optional.
- Admin-only cancellation of approval with a reason, applicant notification and a fresh document-upload draft.
- Admin-only account closure that blocks login and revokes sessions while retaining history.
- Duplicate-account evidence showing application links, account review status, authorized name details and identical uploaded files.

## Matching rules

A successful document-consistency match requires **all** of these:

1. The applicant's name on the PAN card matches the name on Aadhaar.
2. Every distinct salary slip's employee name matches both Aadhaar and PAN.
3. Every distinct salary slip contains a readable PAN number equal to the PAN number on the uploaded PAN card.
4. Required documents and required readable name/number evidence are available.

Names are compared using application-scoped tokens derived from locally normalized values (case, whitespace and punctuation normalization). OCR-only I/l/1 and O/0 ambiguities are flagged as Incomplete for review, rather than forced into a match or a definite mismatch. Reordered names, initials and fuzzy similarities are reviewer hints, not automatic matches. PAN values are normalized locally before token comparison. Father's names are **not** matching criteria.

- **Match:** required name and salary-slip PAN comparisons agree.
- **Mismatch:** an extracted required name or salary-slip PAN number differs.
- **Incomplete:** required evidence is absent/unreadable or extraction could not be validated.
- **Identifier warning:** format/checksum or OCR-confidence concerns are shown separately. Synthetic/untrusted identifiers do not change an otherwise matching consistency result and are not promoted into verified identity records.
- **Human decision:** a match does not automatically approve the application. Cross-account reuse of an identity identifier or identical file is automatically rejected only when it appears on another active account’s current approved application, even if names match or identifiers are synthetic/unvalidated. Pending, rejected, closed and superseded approvals do not trigger this policy. The earlier account’s decision stays unchanged. Applicants receive a reason and support guidance; staff see scoped duplicate-account evidence. Same-account resubmissions are allowed.

Identical duplicate salary files are counted once. Repeated pay periods are flagged for review. Bank statements are informational and never replace salary slips. The name entered during account registration is informational; documentary names take precedence.

## Duplicate documents and approved accounts

Duplicate rejection is separate from name/PAN consistency: a submission can have matching names and still be rejected for reuse of an approved account's documents.

- A cross-account conflict or automatic rejection requires the same extracted Aadhaar/PAN identifier, or an identical submitted file, on **another active account's current approved application**.
- Another account's pending, rejected or changes-requested application does not cause duplicate rejection. Closed accounts and historical approvals superseded by a fresh-verification draft do not qualify either.
- Synthetic/untrusted identifiers can trigger this policy when another account's current application is approved. Approval still does not authenticate those identifiers with government records.
- Same-account uploads and revisions do not trigger cross-account rejection.
- Required name/PAN matching and human review still apply when no approved duplicate exists. Matching does not guarantee approval.
- Approval checks run again when staff choose Approve. If two submissions were pending and one has since been approved, staff cannot approve the other with the same identity/documents; reprocess the second submission to apply the duplicate decision.

The rejected applicant receives an automatic rejection stamp, an in-app notification and a message explaining that the documents or identifiers are already used by an approved account, with sign-in/support guidance. The approved account owner and scoped administrators receive notifications. The earlier approval is unchanged.

Staff can inspect **Identity** for related applications, review statuses, account names and extracted document names according to their permissions, plus identical-file evidence. Other submissions may appear here for information without constituting a blocking conflict. Applicants are not shown another account's personal details. Reuse alone is not presented as proof of forgery.

Existing final decisions are retained when the policy changes. A previously rejected applicant must start a new revision/submission; code updates do not silently reopen rejected or approved applications.

## Admin approval cancellation and account closure

In a staff application, an administrator can select **Cancel approval / require fresh documents** after approval. A reason is required. The earlier approval remains in history, while the current application becomes an empty draft requiring fresh Aadhaar, PAN and salary-slip uploads, processing and staff approval. The applicant receives a notification and instructions. This is a manual admin action; no automatic expiry interval is configured.

**Close account** blocks applicant login, revokes all sessions and cancels current approval. It retains review and audit history and identity reuse records. It does not permanently erase personal data. A closed account no longer qualifies as an active approved account for duplicate rejection. Account-wide closure requires admin access to every organization associated with the applicant.

## Document extraction and privacy

The processing workflow validates files and scan quality, reads embedded PDF text or runs local Tesseract OCR, validates its structure with Pydantic, masks personal data, checks the outbound payload, requests Gemini structured extraction, records privacy-safe call telemetry, validates cited evidence, compares names/PAN numbers, checks approved-account reuse, and routes the result to staff or applies the duplicate rejection policy.

- PAN holder extraction uses Name labels and page positions. It supports older layouts with names above the PAN number, PDF text whose object order differs from visual order, and indented values below labels. Father-name and signature regions are excluded from holder-name selection.
- Aadhaar holder extraction uses labelled evidence or the name region near date of birth. OCR character ambiguities remain reviewable; the parser does not force uncertain readings to match.
- Identity fields use document-specific labels such as **Aadhaar number**. Employer and salary fields are shown for salary slips rather than requested from Aadhaar or PAN.
- Date masking covers years starting with 18, 19 or 20, including synthetic dates such as 1895. Numeric masking also removes partial date fragments that previously caused privacy-gate failures.
- Only masked text is sent to Gemini. Raw documents, unmasked names and identity numbers stay within the local processing/storage flow. A failed privacy gate prevents the external extraction request; it is an Incomplete/manual-review outcome, not a name mismatch.
- Extracted holder names and identifiers must refer to valid tokens and cited evidence lines. Missing or ambiguous holder evidence cannot be treated as a successful comparison.

## Architecture and models

```text
Applicant / Staff browser (React + TypeScript, port 5173)
                     |
                  Vite /v1 proxy
                     |
             FastAPI API (port 8000)
               |                 |
        SQL database      Encrypted file storage
        + durable jobs       (local by default)
               |
          Python worker
               |
  Validate -> Inspect -> PyMuPDF/Tesseract -> Presidio/spaCy
               |
      Tokenization + outbound privacy gate
               |
        Gemini (masked text only)
               |
   Schema/evidence validation -> local comparison rules
               |
       Staff review -> decision -> applicant notification
```

- **OCR:** PyMuPDF reads embedded PDF text; Tesseract reads images/scanned PDFs locally. OCR is separate from Gemini.
- **NLP:** Microsoft Presidio uses spaCy `en_core_web_sm` plus custom recognizers for identifiers and labelled fields.
- **LLM:** `google-genai` calls the model configured in `GEMINI_MODEL`; the repository default is `gemini-3.5-flash-lite`. Access depends on the reviewer's Google API key/account. Set another supported model if that configured model is unavailable.
- **Validation:** Pydantic validates the structured response, token types and cited line evidence. Values must be present in the submitted masked payload.
- **Matching:** deterministic Python comparisons make the match decision; Gemini does not decide whether documents match or approve applications.
- **Database models:** users, organizations, staff memberships, policies, applications, versions, documents, extraction results, verification checks, jobs/events, identity claims, reviews, notifications and audit events. See `backend/app/models/`.

## Prerequisites and dependencies

Install these before starting:

- **Git** to clone the repository.
- **Python 3.12** (required: `>=3.12,<3.13`). `uv` can download it automatically.
- **uv** for the locked Python environment.
- **Node.js 22 or 24 with npm**. The Docker build uses Node 22; local work in this repository has used Node 24.
- **Tesseract OCR** with English (`eng`) language data. Installing `pytesseract` alone does not install this executable.
- **Google Gemini API key** for normal automated extraction.
- **Database:** SQLite is sufficient for local review; PostgreSQL is the alternative for multi-worker/server use.
- **Internet:** needed for initial dependency/model downloads and Gemini requests. The spaCy model is installed during dependency setup, not downloaded during processing.

### System installation

**macOS (Homebrew):**

```bash
brew install uv node tesseract
```

On Intel Macs, Homebrew may build dependencies from source and can fail because that platform is no longer supported by recent Homebrew releases. A working existing Tesseract installation can be reused; Docker is another option if available.

**Windows PowerShell:**

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id UB-Mannheim.TesseractOCR -e
```

Reopen the terminal after installing tools so PATH changes take effect. Check that the installed Node version is compatible.

**Ubuntu/Debian:** install Git, Tesseract and English data using your package manager, and install uv and Node 22/24 using your preferred toolchain installer. Distribution-default Node versions may differ.

```bash
sudo apt-get update
sudo apt-get install -y git tesseract-ocr tesseract-ocr-eng
```

Confirm the tools are visible:

```bash
uv --version
node --version
npm --version
tesseract --version
tesseract --list-langs
```

On Windows, if Tesseract is not on PATH, invoke its full installed path and set `TESSERACT_CMD` below.

### Application packages

`uv sync --locked` installs **all backend runtime and development packages**, including the pinned spaCy model. `npm ci` installs **all frontend runtime and development packages**.

Backend packages include FastAPI, Uvicorn, Pydantic/settings, SQLAlchemy, Alembic, psycopg, Argon2, cryptography, multipart/email validation, PyMuPDF, pytesseract, Pillow, OpenCV, NumPy, Presidio, spaCy, google-genai, RapidFuzz, structlog and OpenTelemetry. Development tools include pytest, pytest-timeout, HTTPX, ReportLab and Ruff.

Frontend packages include React/React DOM, React Router, TanStack Query, Radix UI, React Hook Form, Zod, Lucide, Sonner, Tailwind CSS and utility/style/font packages. Build/test tools include TypeScript, Vite, ESLint, Vitest, Testing Library, jsdom and Playwright.

The complete direct-package inventory is in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md). Exact resolved/transitive versions are recorded in `backend/uv.lock` and `frontend/package-lock.json`; commit both lockfiles.

## Local setup

The commands below use a local SQLite database for the shortest reviewer setup. Do not use the author's local `.env`, API key, administrator account or database.

### 1. Clone the project

Replace `YOUR_GITHUB_REPOSITORY_URL` with the actual repository URL:

```bash
git clone YOUR_GITHUB_REPOSITORY_URL northbridge-kyc
cd northbridge-kyc
```

All subsequent terminal paths are relative to this repository root.

### 2. Install locked dependencies

```bash
cd backend
uv python install 3.12
uv sync --locked
cd ../frontend
npm ci
cd ..
```

The Python environment is `backend/.venv`; frontend packages live in `frontend/node_modules`. Closing VS Code does not remove either environment. Re-run installation commands after pulling dependency/lockfile changes.

### 3. Create your local environment file

**macOS/Linux:**

```bash
cp backend/.env.example backend/.env
mkdir -p backend/var
```

**Windows PowerShell:**

```powershell
Copy-Item backend/.env.example backend/.env
New-Item -ItemType Directory -Force backend/var | Out-Null
```

Only copy once: do not overwrite an already configured `.env`.

Open `backend/.env` and set these local-development values:

```dotenv
APP_ENV=development
PUBLIC_BASE_URL=http://localhost:5173
ALLOWED_ORIGINS=["http://localhost:5173"]

DATABASE_URL=sqlite:///./var/dev.db
MIGRATION_DATABASE_URL=
ALLOW_SQLITE=true

SESSION_SECRET=
ENCRYPTION_KEY=
IDENTITY_HMAC_SECRET=

LLM_PROVIDER=gemini
GEMINI_API_KEY=YOUR_OWN_GEMINI_API_KEY
GEMINI_MODEL=gemini-3.5-flash-lite

TESSERACT_CMD=
TESSERACT_LANG=eng
OCR_TIMEOUT_SECONDS=30

PAN_POLICY=required
STORAGE_BACKEND=local
STORAGE_DIR=./var/storage
S3_BUCKET=
S3_ENDPOINT_URL=
```

- `ALLOWED_ORIGINS` is shown as a JSON array to avoid settings-loader decoding errors.
- Leave **`MIGRATION_DATABASE_URL` empty for SQLite**. Otherwise a copied PostgreSQL placeholder can redirect migrations to the wrong database.
- Leave `TESSERACT_CMD` empty if Tesseract is on PATH. Otherwise use the result of `which tesseract` (macOS/Linux), or your Windows executable path, e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- Keep remaining limits and settings from `.env.example` unless you need to change them.
- Local storage is encrypted at rest. Files are stored under `backend/var/storage` when the backend is launched from `backend`.

Generate your own secrets:

```bash
cd backend
uv run python -m app.cli gen-secrets
```

**Copy the three generated lines into `backend/.env`.** This command prints secrets; it does not automatically save them. Keep these keys for the lifetime of the local database/storage. Changing the encryption key makes previously encrypted data unreadable.

### 4. Create the database schema

While still inside `backend`:

```bash
uv run python -m app.cli migrate
uv run python -m app.cli preflight
```

Run migrations before preflight on a fresh checkout, because preflight checks that migrations are current. Resolve any `[FAIL]` results before processing documents. Missing SMTP settings are expected in development; mail is captured locally.

### 5. Create an administrator

```bash
uv run python -m app.cli bootstrap-admin --email "reviewer@example.com" --name "Local Reviewer"
```

Use your own valid email address. Enter and confirm a strong password when prompted; typed characters are hidden. No administrator credentials are hardcoded. The account is saved in your configured database. Admin management also exists in the staff portal.

### PostgreSQL alternative

Instead of SQLite, configure an existing PostgreSQL database:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/northbridge_kyc
MIGRATION_DATABASE_URL=
ALLOW_SQLITE=false
```

For Neon, use a pooled runtime URL, a direct migration URL and TLS (`sslmode=require`). Then run the same migrations, preflight and admin creation commands. SQLite must use **exactly one worker**; use PostgreSQL for concurrent workers. Credentials above are placeholders, not a ready-made server.

## Run in three terminals

Open three terminals **at the repository root**. Keep all three running.

### Terminal 1 - Backend API

```bash
cd backend
uv run uvicorn app.main:app --port 8000 --reload
```

### Terminal 2 - Processing worker

```bash
cd backend
uv run python -m app.cli worker
```

Wait for `[worker] ready` / `polling for jobs`. Uploads can work without the worker, but queued documents will not be processed.

### Terminal 3 - Frontend

```bash
cd frontend
npm run dev
```

Open these URLs:

- Applicant portal: **http://localhost:5173**
- Staff/admin login: **http://localhost:5173/staff/login**
- API documentation: **http://localhost:8000/docs**
- API health: **http://localhost:8000/health**
- Development mailbox: **http://localhost:5173/dev/mailbox** (development only, when SMTP is not configured)

Port 8000 is the API, not the React application. If Vite selects a different port because 5173 is busy, free 5173 or update the frontend origins/base URL accordingly.

To restart a process, select its terminal, press **Control+C**, and rerun its command. API Python changes reload with `--reload`; restart the worker after backend processing changes and restart processes after `.env` changes. Refresh the browser after frontend changes.

## Reviewer walkthrough

1. Start all three processes and sign in to the applicant portal using a newly registered applicant account.
2. Follow the verification email in the development mailbox (or configured SMTP delivery).
3. Upload Aadhaar, PAN and a salary slip that explicitly prints the applicant name and PAN number. Preview each file/page before submitting.
4. Use identical documentary names and PAN numbers for a positive test. Synthetic examples are acceptable; placeholder format warnings are informational.
5. Submit with consent. The worker performs processing in the background. Applicants see a simple result; staff see the detailed timeline.
6. Sign in at `/staff/login` using the administrator created above. Open **Review queue**, then the application.
7. Inspect applicant details, originals, extracted evidence, name comparisons and salary-slip PAN comparison. Claim the review if using a reviewer account.
8. Choose **Approve**, **Request changes**, or **Reject**. Correction requests need an instruction of at least 5 characters and an applicant message of at least 10 characters. Rejection requires a reason and a message of at least 10 characters.
9. After approval, the applicant sees **KYC completed and approved**, an approval stamp/date and an in-app notification. Rejection shows a rejection stamp and the reason. The overview and queues refresh automatically.
10. To test duplicate handling, submit the same identity from a second account before the first is approved: both should remain eligible for review when their own matching checks pass. Approve one account, then submit or reprocess the other: expect automatic rejection for reuse. Inspect the Identity tab and notifications.
11. On an approved application, use **Cancel approval / require fresh documents**, enter a reason and confirm. Verify that the applicant receives a notification and an empty draft requiring fresh uploads. Use **Close account** separately to block login while retaining history.
12. Test a negative case by changing a salary slip name or PAN number. Expect Mismatch. Remove the printed PAN from a salary slip to test Incomplete. For a rejected/corrected submission, start a correction in the applicant portal and resubmit a new version.

Existing results do not automatically rerun when processing code changes. Staff can use **Reprocess documents** on eligible undecided submissions. Existing successful results generated under older criteria cannot bypass the newer approval checks.

## Tests

**Backend (from `backend`):**

```bash
uv run pytest -q
```

Tests use isolated databases/storage and the explicitly labelled scripted extraction test double. They do not require real personal documents or call Gemini by default. OCR tests need Tesseract. PostgreSQL tests need a dedicated `TEST_POSTGRES_URL` and otherwise skip. Never point tests at a database containing real applicant data.

A known environment-sensitive failure is `test_skewed_and_exif_rotated_image`: JPEG DPI metadata can be reported differently by Pillow. To run the remaining checks while investigating that known failure:

```bash
uv run pytest -q -k "not skewed_and_exif_rotated_image"
```

**Frontend (from `frontend`):**

```bash
npm test
npm run typecheck
npm run build
npm run lint
```

Frontend lint currently reports Fast Refresh warnings. Backend Ruff currently has existing style findings; do not assume the entire repository is lint-clean.

### Verification scope (11 November 2026)

Recent checks were run at different stages of the updates; these are not a single full-suite result for the final code:

- PAN parsing, privacy and PAN-requirement tests: **44 passed, 1 deselected**, including historical birth years and the PAN privacy-block regression. The affected uploaded PAN was also checked locally for privacy-gate approval and holder-name evidence without an external Gemini call.
- OCR formats, matching and smoke checks: **20 passed, 1 deselected** during the parsing changes.
- Duplicate-policy, identity, account-lifecycle and review checks: **20 passed**, followed by **8 duplicate-policy tests passed** after adding approval-time and closed-account cases. These runs overlap.
- Frontend: **27 tests passed** during the admin lifecycle changes. TypeScript checking also passed after the latest duplicate-policy UI update.
- Docker, S3, SMTP, a fresh machine installation and live PostgreSQL were not revalidated for these changes. Live Gemini tests remain opt-in. The PDF report and other generated documentation may predate the latest behavior; this README describes the current workflow.

**Optional browser tests:**

```bash
npx playwright install chromium
npm run e2e
```

These require a separately running stack in test mode with a dedicated database. Read [docs/SETUP.md](docs/SETUP.md) and `scripts/e2e-env.sh` / `scripts/e2e-env.ps1` first. Starting normal development services alone does not configure the E2E test stack.

The Gemini live test is opt-in (`live_gemini`) and requires `GEMINI_API_KEY_LIVE`; inspect the test before running it. Google API usage may consume quota. See the verification scope above for the checks actually run; a previous build or report does not verify later changes.

## Docker alternative

Docker Desktop/Engine with Compose is optional. The repository includes PostgreSQL, migration, API, worker and Nginx/frontend services.

1. Configure `backend/.env` with your secrets and Gemini key. For Docker, use `PUBLIC_BASE_URL=http://localhost:8080`, `ALLOWED_ORIGINS=["http://localhost:8080"]`, `STORAGE_DIR=/data/storage`, and leave `TESSERACT_CMD` empty (the image installs it).
2. Leave `MIGRATION_DATABASE_URL` empty for the bundled PostgreSQL service. Compose supplies the service database URL unless a shell/root `.env` variable overrides it. `backend/.env` is an `env_file`, not the source of Compose `${DATABASE_URL}` interpolation.
3. Run from the repository root:

```bash
docker compose up --build -d
docker compose exec api python -m app.cli bootstrap-admin --email "reviewer@example.com" --name "Local Reviewer"
```

Open **http://localhost:8080** or **http://localhost:8080/staff/login**. The API is proxied through Nginx; port 8000 is not published by this Compose file. Stop with `docker compose down`; deleting named volumes also deletes the database/storage. Docker startup was not executed as part of this documentation update.

## Troubleshooting

- **`No module named app`:** run backend commands inside `backend`, not from the project root or parent Downloads directory.
- **Python 3.14/another environment appears:** return to `backend`, run `uv sync --locked`, then use `uv run ...`.
- **`unable to open database file`:** create `backend/var`, check SQLite permissions and launch commands from `backend`.
- **`allowed_origins` JSON error:** use `ALLOWED_ORIGINS=["http://localhost:5173"]`.
- **Tesseract not found:** verify `tesseract --version` or set its executable path in `TESSERACT_CMD`; restart API/worker.
- **spaCy model missing:** run `uv sync --locked` from `backend`. The model is part of the locked dependencies.
- **Queued forever:** start Terminal 2 and wait for the worker-ready message.
- **Privacy gate blocked:** do not bypass masking. Date handling now includes 1800s synthetic dates and partial numeric fragments. Restart the worker after updating and reprocess an eligible submission. If it still fails, inspect worker logs or request manual review.
- **Gemini model/key/quota/network error:** confirm the configured model is accessible to your API key and check worker logs. Preflight checks key presence, not Google account permissions.
- **Extraction invalid:** provider output failed schema or evidence validation; it is not proof that document names differ.
- **Synthetic identifiers shown as unvalidated:** informational. Matching still checks documentary names and salary-slip PAN equality. Cross-account identifier or identical-file reuse triggers rejection only against a current approved application on another active account.
- **Old mismatch/result still visible:** restart the worker, refresh, and reprocess the eligible application. New extraction code does not rewrite previous attempts automatically.
- **Duplicate shown but no rejection:** expected when the other account is not currently approved. Only active current approvals trigger the duplicate rejection policy.
- **Fresh verification required:** the administrator cancelled approval. Upload fresh required documents into the new draft and submit for processing and review.
- **Account closed:** login is disabled; the administrator retained the account and review history rather than permanently erasing it.
- **Awaiting review:** processing finished; a human must decide in the staff portal.
- **Decision form errors:** read the inline message, complete all required text and reload if another user/version changed the application.
- **Time seems wrong:** refresh to load timezone-aware code. Event timestamps show when that event occurred, not the current clock time.

## GitHub preparation

Before pushing, include source, documentation, manifests and lockfiles. Do **not** upload local secrets, applicant documents or your local database:

- Keep `backend/.env`, credentials/API keys, `backend/var/`, `.venv/`, `node_modules/` and generated runtime/test data out of version control. The repository `.gitignore` covers the principal local paths.
- Share `.env.example` with empty secrets/placeholders; every reviewer should create their own `.env` and admin account.
- Keep only clearly synthetic fixtures in `samples/`; do not add uploaded applicant files or screenshots containing real personal details.
- Review `git status` and staged files before committing. If a credential was previously committed, ignoring it later does not remove it from Git history; rotate it and clean the history as appropriate.
- The repository does not currently declare a license file; choose one before presenting it as licensed open-source software.

## Project layout and documentation

```text
backend/
  app/api/              Auth, applicant, document, staff and admin endpoints
  app/auth/             Sessions, authorization and password handling
  app/core/             Configuration, encryption and database setup
  app/models/           SQLAlchemy entities and status enums
  app/pipeline/         OCR, masking, Gemini, evidence validation and matching
  app/services/         Views, identity warnings, notifications, policy and retention
  app/workers/          Durable job queue and processing worker
  app/storage/          Encrypted local/S3 file storage
  alembic/              Database migrations
  tests/                Backend regression/integration tests
frontend/src/           React applicant/staff UI, components and tests
scripts/                Synthetic data, evaluation and test helpers
samples/                Generated synthetic fixtures (create with the command below)
tests/e2e/              Playwright journeys
docs/                   Technical documentation and dependency inventory
output/pdf/             Project report PDF
```

- [Project report (PDF)](output/pdf/Northbridge_KYC_Project_Report.pdf)
- [Complete direct dependencies](docs/DEPENDENCIES.md)
- [Detailed setup and E2E instructions](docs/SETUP.md) - use this README for the current matching rules/local quick start.
- [Architecture](docs/ARCHITECTURE.md)
- [API guide](docs/API.md) and [OpenAPI schema](docs/openapi.json)
- [Security and retention](docs/SECURITY.md)
- [Synthetic evaluation](docs/EVALUATION.md) - historical evaluation, not a fresh measurement of the latest rules.

**Synthetic sample generation:** from `backend`, run `uv run python ../scripts/generate_samples.py` to regenerate the repository samples. This creates or updates files under `samples/`; the directory may not exist in a fresh checkout. Newly generated salary slips include PAN numbers; already-existing fixture files are not silently rewritten when code changes.
# northbdrige_kyc
