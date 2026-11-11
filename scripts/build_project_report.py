"""Build the reviewer project report and direct-dependency inventory.
Run from backend: uv run python ../scripts/build_project_report.py
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    Preformatted,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'pdf'
OUT.mkdir(parents=True, exist_ok=True)
PDF = OUT / 'Northbridge_KYC_Project_Report.pdf'
TEAL = colors.HexColor('#12343b')
ACCENT = colors.HexColor('#5b3fa0')
MUTED = colors.HexColor('#52666c')
LIGHT = colors.HexColor('#edf3f2')
WIDTH = 174 * mm
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='ReportTitle', fontName='Helvetica-Bold', fontSize=32, leading=36, textColor=TEAL, spaceAfter=16))
styles.add(ParagraphStyle(name='SectionTitle', fontName='Helvetica-Bold', fontSize=22, leading=27, textColor=TEAL, spaceAfter=13))
styles.add(ParagraphStyle(name='Subhead', fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=ACCENT, spaceBefore=11, spaceAfter=6))
styles.add(ParagraphStyle(name='Text', fontName='Helvetica', fontSize=10, leading=14, textColor=TEAL, spaceAfter=8))
styles.add(ParagraphStyle(name='SmallText', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=MUTED, spaceAfter=6))
styles.add(ParagraphStyle(name='Cell', fontName='Helvetica', fontSize=8, leading=10.5, textColor=TEAL))
styles.add(ParagraphStyle(name='CellHead', fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=colors.white))
styles.add(ParagraphStyle(name='CodePanel', fontName='Courier', fontSize=8.3, leading=11.5, backColor=LIGHT, borderPadding=9, spaceBefore=5, spaceAfter=12))
styles.add(ParagraphStyle(name='Kicker', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=ACCENT, spaceAfter=12))
story = []


def p(text, style='Text'):
    story.append(Paragraph(text, styles[style]))


def h(text):
    p(text, 'Subhead')


def bullets(items):
    for item in items:
        story.append(Paragraph('- ' + item, styles['Text']))


def code(text):
    story.append(Preformatted(text.strip(), styles['CodePanel'], maxLineLength=86))


def page(number, title):
    if story:
        story.append(PageBreak())
    p(f'PROJECT GUIDE / {number:02d}', 'Kicker')
    p(title, 'SectionTitle')


def table(headers, rows, widths):
    data = [[Paragraph(escape(str(x)), styles['CellHead']) for x in headers]]
    data += [[Paragraph(escape(str(x)), styles['Cell']) for x in row] for row in rows]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, TEAL),
    ]))
    story.append(t)


def footer(canvas, doc):
    canvas.saveState()
    w, height = doc.pagesize
    canvas.setStrokeColor(colors.HexColor('#cbd7d6'))
    canvas.line(18 * mm, 18 * mm, w - 18 * mm, 18 * mm)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 12 * mm, 'NORTHBRIDGE KYC  |  Repository review guide  |  11 November 2026')
    canvas.drawRightString(w - 18 * mm, 12 * mm, str(doc.page))
    canvas.restoreState()


pyproject = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text())
package = json.loads((ROOT / 'frontend/package.json').read_text())
groups = [
    ('Backend runtime', [(x.split(' @ ')[0], x.split(' @ ')[1] if ' @ ' in x else x) for x in pyproject['project']['dependencies']]),
    ('Backend development', [(x, x) for x in pyproject['dependency-groups']['dev']]),
    ('Frontend runtime', list(package['dependencies'].items())),
    ('Frontend development', list(package['devDependencies'].items())),
]
# Keep the GitHub inventory useful independently of the PDF.
lines = ['# Dependency inventory', '', 'Generated from `backend/pyproject.toml` and `frontend/package.json`.',
         'Exact resolved and transitive versions live in `backend/uv.lock` and `frontend/package-lock.json`.', '',
         '## System prerequisites', '', '- Python >=3.12,<3.13; uv; Git.', '- Node 22 or 24 with npm.',
         '- Tesseract OCR executable and English language data.', '- SQLite for local development, or PostgreSQL for server/multi-worker use.',
         '- Gemini API access for normal automated extraction.', '- Optional: Docker/Compose; Playwright Chromium; boto3 plus an S3-compatible bucket for S3 storage.', '']
for title, rows in groups:
    lines += ['## ' + title, '']
    for name, version in rows:
        lines.append(f'- `{name}`: `{version}`' if title.startswith('Frontend') else f'- `{version}`')
    lines.append('')
lines += ['## Installation', '', '```bash', 'cd backend', 'uv sync --locked', 'cd ../frontend', 'npm ci', '```', '',
          'The pinned spaCy model is downloaded from the URL declared in pyproject.toml during setup.',
          'Tesseract must be installed separately at the OS level. boto3 is optional and is not included in the default lockfile.', '',
          'Regenerate this inventory and the project report with `uv run python ../scripts/build_project_report.py` from `backend`.']
(ROOT / 'docs/DEPENDENCIES.md').write_text('\n'.join(lines) + '\n')

page(1, 'Northbridge KYC')
p('PROJECT REPORT &amp; LOCAL REVIEW GUIDE', 'Kicker')
p('Document consistency.<br/>Local extraction.<br/>Human decisions.', 'ReportTitle')
p('A full-stack application for reviewing applicant identity and payroll documents. It compares documentary names and salary-slip PAN numbers, then requires an authorized staff decision.')
story.append(Spacer(1, 12 * mm))
table(['Applicant submits', 'System evaluates', 'Staff decides'], [
    ['Aadhaar + PAN + one or more salary slips. Optional bank statement.',
     'Names agree across documents; each salary-slip PAN equals the uploaded PAN.',
     'Approve, reject, or request corrections after reviewing evidence.'],
], [WIDTH / 3] * 3)
h('Purpose and audience')
p('This guide is for a GitHub reviewer or developer who needs to understand the implemented features, run the application locally, inspect the architecture and evaluate the current behavior.')
h('Important scope')
p('Approval confirms document consistency only. It is not government authentication, a guarantee that documents are genuine, or a loan/credit decision. Synthetic format warnings are informational; untrusted sample identifiers are not promoted into verified identity records.')
p('Prepared from the repository on 11 November 2026. README.md is the primary runnable setup guide. The report contains no local secrets, real applicant documents or administrator credentials.', 'SmallText')

page(2, 'Features and roles')
table(['Role', 'Implemented capabilities'], [
    ['Applicant', 'Register/sign in; verification and password reset; mandatory Aadhaar/PAN uploads; salary slips; optional bank statement; page previews; replacement/removal; consent; versioned corrections; simple results; approval/rejection stamps; notifications.'],
    ['Reviewer', 'Organization-scoped queue; assignment and release; live processing stages; masked evidence; authorized reveals/originals; name/PAN comparison; internal notes; approval, rejection and correction requests.'],
    ['Administrator', 'Full applicant profile within authorized organizations; direct privileged document access; reviewer features; staff management; policy limits; operational metrics; audited identity conflict resolution.'],
], [30 * mm, 144 * mm])
h('Recent additions reflected in the code')
bullets([
    'Mandatory PAN upload and salary-slip PAN-number equality checks.',
    'Applicant document previews before submission, including multiple pages and original files.',
    'Staff document inventory with current stage, errors, evidence boxes and exact-field reveals.',
    'Applicant match summaries without exposing detailed technical processing stages.',
    'KYC completed-and-approved UI, shared decision stamps and in-app notifications.',
    'Dynamic overview counts aligned with queue filters; five-second polling for current views.',
    'Timezone-aware persistence/display; rejection after resubmission handles retention dates.',
    'Column-aware OCR and checks that prevent identifiers being accepted as names.',
])

page(3, 'End-to-end workflow')
steps = [
    ('1. Prepare account', 'Applicant registers, verifies email and opens a saved draft.'),
    ('2. Upload and review', 'Aadhaar, PAN and salary slips are uploaded. Each file can be previewed before consent/submission.'),
    ('3. Queue processing', 'API creates a durable process_version job. The separate worker claims it.'),
    ('4. Extract safely', 'Local OCR/text extraction, masking and an outbound privacy gate precede the Gemini call.'),
    ('5. Validate and compare', 'The response must satisfy schema, token-type and line-evidence rules. Local rules compare names and PAN numbers.'),
    ('6. Human review', 'Staff inspect the originals, extracted fields, checks and warnings, then record a decision.'),
    ('7. Notify applicant', 'Applicant sees the result, decision stamp and in-app notification. Corrections produce a new version.'),
]
for title, body in steps:
    h(title)
    p(body)
h('Versioning and reprocessing')
p('Submitted versions are immutable to the applicant. Starting a correction creates a new current draft and supersedes the previous version. Worker attempts are versioned; old results do not silently become the latest result after a code change. Staff can reprocess eligible undecided submissions. Approval uses version/row-version checks to reject stale decisions.')

page(4, 'Architecture and runtime services')
code('''Browser: React / TypeScript (5173)
          |
          | authenticated /v1 requests through Vite proxy
          v
FastAPI API (8000) -----> SQL database + durable job queue
          |                              |
          v                              v
Encrypted blob storage             Python worker
(local default / optional S3)             |
                        Local OCR -> masking -> privacy gate
                                         |
                           Gemini: masked text only
                                         |
                        validation -> deterministic rules
                                         |
                          staff decision -> notification''')
h('Three processes are intentional')
p('The API handles HTTP requests, authentication and submission. The worker performs document processing outside request handlers. Vite serves the frontend and proxies API requests. Starting the UI and API without the worker leaves documents queued.')
h('Persistence and communication')
p('SQLite is an explicit local-development choice and supports one worker. PostgreSQL is the intended concurrent-worker database. Processing events support authenticated server-sent events with polling fallback. The overview/queue and applicant views also poll for current data. File contents are encrypted in private storage, not exposed as static URLs.')
h('Alternative deployment')
p('Docker Compose defines PostgreSQL, one-shot migration, API, worker and Nginx/web services. It serves the application at port 8080. Review README.md before using external PostgreSQL with Compose: backend env_file values are distinct from Compose interpolation variables.')

page(5, 'OCR, NLP and AI models')
table(['Component', 'Model or mechanism', 'Responsibility'], [
    ['Embedded PDF text', 'PyMuPDF', 'Read text and page/word geometry locally; no OCR confidence is invented.'],
    ['Image/scanned PDF OCR', 'Tesseract, eng; OpenCV/Pillow preprocessing', 'EXIF handling, grayscale/deskew/upscale and bounded alternate OCR when needed.'],
    ['Personal-data detection', 'Microsoft Presidio + spaCy en_core_web_sm 3.8.0 + custom rules', 'Recognize names, labels, Aadhaar/PAN/account/contact details; retain coordinates.'],
    ['Tokenization', 'Application-attempt TokenVault', 'Replace values locally with typed placeholders. Normalized equal names/PAN values reuse tokens.'],
    ['Structured extraction', 'google-genai; configured Gemini model', 'Classify documents and copy tokens/amounts with evidence line numbers. Default: gemini-3.5-flash-lite.'],
    ['Validation', 'Pydantic plus local evidence validation', 'Reject invented, mistyped, absent or incorrectly cited values.'],
    ['Comparison', 'Deterministic Python rules; RapidFuzz hints', 'Determine consistency; never delegate match/approval decisions to the LLM.'],
], [31 * mm, 58 * mm, 85 * mm])
h('Outbound boundary')
p('Only a minimized, privacy-gated text payload is sent to Gemini. Raw files, the token vault and original personal values stay in the backend. An uncertain payload is withheld for manual review. Gemini access still requires a supported model, API key, network connectivity and available quota.')
h('Test model')
p('ScriptedProvider is a clearly identified test double, allowed only in APP_ENV=test. It supports reproducible tests without Gemini and is refused as a normal/production fallback. The spaCy model is installed during dependency setup; no NLP model is fetched during a request.')

page(6, 'Matching rules and outcomes')
bullets([
    '<b>Mandatory:</b> Aadhaar, PAN and the configured minimum number of readable salary slips.',
    '<b>Name agreement:</b> PAN holder name equals Aadhaar holder name; each salary slip employee name equals both.',
    '<b>PAN agreement:</b> each distinct salary slip contains a readable PAN value equal to the uploaded PAN card number.',
    '<b>Normalization:</b> case/spacing/punctuation for names; canonical PAN values. Fuzzy/reordered-name hints do not automatically match.',
    '<b>Excluded criteria:</b> father name is not compared; registration name and bank statements are informational.',
])
table(['Scenario', 'Result'], [
    ['Same documentary names; same printed salary-slip PAN', 'Match; staff approval still required.'],
    ['Salary name differs from Aadhaar or PAN', 'Mismatch, with the affected name comparison.'],
    ['Salary-slip PAN differs from uploaded PAN', 'Mismatch, with PAN-number explanation.'],
    ['Salary slip has no readable PAN, or required name is unavailable', 'Incomplete; request clearer/corrected evidence or manual review.'],
    ['Matching synthetic PAN values fail format plausibility', 'Consistency can still match. Identifier warning is informational; no verified identity reservation.'],
    ['Unresolved cross-account identity conflict', 'Staff warning and approval blocker; administrator resolution required.'],
], [113 * mm, 61 * mm])
h('Avoiding false name mismatches')
p('Local extraction splits spatially distant columns so QR-code OCR noise is not appended to a holder name. Identifier-shaped values cannot be treated as person names. Salary employee names need a supported label association; uncertain or ambiguous selection is routed to review instead of inventing a match.')

page(7, 'Data models, privacy and access')
table(['Entity group', 'Models and purpose'], [
    ['Organization/access', 'Organization, OrgPolicy, User, StaffMembership: ownership, roles and configurable limits; PAN remains mandatory.'],
    ['Authentication', 'AuthSession, AuthToken, LoginAttempt: cookie sessions, verification/reset tokens and login protection.'],
    ['Applications', 'Application, ApplicationVersion: references, current/superseded versions, processing/review/check status and concurrency fields.'],
    ['Documents/evidence', 'Document, ExtractionResult, VerificationCheck: storage references, per-page metadata, encrypted exact fields, masked display, provenance and comparison outcomes.'],
    ['Jobs/progress', 'ProcessingJob, ProcessingEvent: durable jobs, attempts, leases, cancellation and stage events.'],
    ['Identity/review', 'IdentityClaim, ConfirmedIdentity, Review: duplicate conflicts, valid identity reservations and audited human decisions. Untrusted samples remain unvalidated.'],
    ['Communication/audit', 'Notification, EmailOutbox, AuditEvent: in-app messages, development/SMTP mail and value-minimized access/action history.'],
], [35 * mm, 139 * mm])
h('Security implementation')
p('Passwords use Argon2. Exact extracted values and document blobs use application-level AES-GCM encryption. Identity fingerprints use a separate keyed HMAC. Access is checked by applicant ownership or staff organization membership. State-changing requests use CSRF protection with cookie sessions. Original/reveal access is privileged and audited; normal evidence views are masked.')
h('Retention and timestamps')
p('Originals are scheduled for deletion after a final decision (default seven days) and removed by retention jobs. Encrypted extraction records remain in the database. Backup deletion is outside the application. UTC timestamps are normalized at the database boundary and displayed in the browser local timezone.')
p('This is an implemented prototype, not a certified security or regulatory assessment. No credentials or applicant files should be committed to GitHub.', 'SmallText')

page(8, 'Local setup: dependencies and configuration')
p('Use README.md for complete OS-specific commands. Run backend commands from backend and frontend commands from frontend. Python must be 3.12; Node 22 or 24 and npm are expected.')
code('''# From repository root
cd backend
uv python install 3.12
uv sync --locked
cd ../frontend
npm ci
cd ..''')
h('System requirements')
p('Git, uv, Python 3.12, Node/npm, Tesseract with eng language data, a Gemini key and a writable database/storage location. SQLite is enough for local review. PostgreSQL, Docker/Compose, Playwright browsers and S3/boto3 are optional according to the selected workflow.')
h('Create a private local environment file')
p('Copy backend/.env.example to backend/.env once. Create backend/var before opening a relative SQLite path. Do not overwrite an existing configured environment file.')
code('''APP_ENV=development
PUBLIC_BASE_URL=http://localhost:5173
ALLOWED_ORIGINS=["http://localhost:5173"]
DATABASE_URL=sqlite:///./var/dev.db
MIGRATION_DATABASE_URL=
ALLOW_SQLITE=true
GEMINI_API_KEY=YOUR_OWN_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
TESSERACT_CMD=
TESSERACT_LANG=eng
STORAGE_BACKEND=local
STORAGE_DIR=./var/storage
PAN_POLICY=required''')
p('Leave TESSERACT_CMD blank if it is on PATH; otherwise set its executable path. Keep remaining limits from the example. In the backend directory, run gen-secrets and paste its three generated values into .env. The command prints secrets; it does not save them.', 'SmallText')

page(9, 'Database, administrator and three terminals')
h('Initialize once, inside backend')
code('''uv run python -m app.cli gen-secrets
# Paste SESSION_SECRET, ENCRYPTION_KEY and IDENTITY_HMAC_SECRET into .env
uv run python -m app.cli migrate
uv run python -m app.cli preflight
uv run python -m app.cli bootstrap-admin --email "you@example.com" --name "Reviewer"''')
p('Use your own email/password. Run migrate before preflight on a fresh database because preflight checks migration status. Preserve encryption/HMAC secrets across restarts. The administrator is stored in the database; it is not hardcoded in the source.')
h('Terminal 1: API (from repository root)')
code('''cd backend
uv run uvicorn app.main:app --port 8000 --reload''')
h('Terminal 2: worker (from repository root)')
code('''cd backend
uv run python -m app.cli worker''')
h('Terminal 3: frontend (from repository root)')
code('''cd frontend
npm run dev''')
table(['URL', 'Use'], [
    ['http://localhost:5173', 'Applicant portal'],
    ['http://localhost:5173/staff/login', 'Reviewer/admin portal'],
    ['http://localhost:8000/docs', 'FastAPI API documentation'],
    ['http://localhost:8000/health', 'API health check'],
    ['http://localhost:5173/dev/mailbox', 'Development mail when SMTP is absent'],
], [100 * mm, 74 * mm])

page(10, 'Reviewer walkthrough and API map')
h('Recommended local review')
bullets([
    'Create a separate applicant account. Open the development mailbox to verify its email.',
    'Upload clearly synthetic Aadhaar, PAN and salary documents with identical names and the same PAN printed on the salary slip. Preview all pages before submitting.',
    'Watch the staff application timeline, inspect exact values and name/PAN comparisons, then approve.',
    'Confirm the applicant receives the approved banner, decision stamp and in-app notification.',
    'Change a salary name or PAN for a mismatch case; omit a printed salary PAN for an incomplete case.',
    'Request corrections or reject with an explanatory message. A correction produces a new submission version.',
])
h('Endpoint families')
table(['Routes', 'Purpose'], [
    ['/v1/auth/*', 'Registration, sessions, verification and password-reset flows.'],
    ['/v1/applications/*', 'Applicant draft, upload, submit, revise, cancel and progress operations.'],
    ['/v1/documents/{id}/pages/{page}', 'Authorized page previews; reveal=true requires ownership/privilege.'],
    ['/v1/documents/{id}/original and /reveal', 'Authorized originals and exact extracted fields.'],
    ['/v1/staff/dashboard and /applications/*', 'Overview, queue, details, assignment, decisions and reprocessing.'],
    ['/v1/admin/*', 'Staff, policies, metrics and identity-conflict administration.'],
    ['/v1/notifications/*', 'Applicant/staff in-app notifications and read state.'],
], [94 * mm, 80 * mm])
p('Interactive endpoint contracts are available at /docs, with a repository snapshot in docs/openapi.json. Browser clients must maintain the session cookie and CSRF token for state-changing requests.', 'SmallText')

page(11, 'Verification, limitations and troubleshooting')
h('How to verify locally')
code('''# backend directory: isolated test DBs, scripted extraction by default
uv run pytest -q
# Known Pillow/DPI issue can be excluded while it is investigated:
uv run pytest -q -k "not skewed_and_exif_rotated_image"

# frontend directory
npm test
npm run typecheck
npm run build
npm run lint''')
p('Backend tests cover document validation, masking/privacy gates, evidence validation, matching/PAN requirements, authorization, review actions, queues, notifications, retention and timestamps. Frontend tests cover result states, decisions, previews, forms, timeline behavior and time formatting. PostgreSQL tests skip without a dedicated test database; live Gemini is opt-in.')
h('Latest recorded verification - 11 November 2026')
p('Backend filtered run: 104 passed, 6 skipped, 2 deselected. Frontend: 24 tests passed and TypeScript checking passed. The known image-DPI case was excluded, live Gemini remains opt-in and PostgreSQL tests require a separate test database.')
h('What is and is not verified')
p('Recent local work exercised backend regression tests with isolated SQLite, Tesseract OCR cases, frontend tests/type checking and production builds. A synthetic-placeholder Gemini request verified the corrected response schema. This report does not claim that every uploaded document or Gemini model response has been independently verified. Docker, fresh Windows setup, S3/SMTP infrastructure and live PostgreSQL were not re-executed for this report.')
p('Known limitations: one image-DPI metadata test is environment-sensitive and currently excluded in filtered runs; frontend lint has Fast Refresh warnings; backend Ruff has existing style findings. OCR and model outputs can still fail validation and require review. Existing sample files may predate the PAN-in-salary rule; regeneration is explicit, not automatic.')
h('Common setup failures')
p('<b>No module named app:</b> run inside backend. <b>SQLite cannot open:</b> create backend/var and use the correct working directory. <b>Queued forever:</b> start the worker. <b>Origins parsing:</b> use a JSON array. <b>Model/key error:</b> check Google access, quota and worker logs. <b>Awaiting review:</b> a human decision is required. <b>Old result:</b> restart the worker and reprocess eligible versions.')

page(12, 'Dependency inventory: backend')
p('These are direct constraints from pyproject.toml, not claims about the newest package release. uv.lock contains exact resolved and transitive packages. uv sync --locked installs both runtime and dev groups, including the spaCy model.')
for title, rows in groups[:2]:
    h(title)
    formatted = []
    for name, version in rows:
        if 'github.com' in version:
            formatted.append(['en-core-web-sm', '3.8.0 wheel from explosion/spacy-models on GitHub (URL pinned in pyproject.toml)'])
        else:
            formatted.append([version, 'Installed by uv sync --locked'])
    table(['Dependency / constraint', 'Installation'], formatted, [103 * mm, 71 * mm])

page(13, 'Dependency inventory: frontend')
p('Direct constraints from package.json. npm ci uses package-lock.json for the exact resolved/transitive package graph. No manual per-package installation is necessary.')
for title, rows in groups[2:]:
    h(title)
    table(['Package', 'Declared version'], rows, [128 * mm, 46 * mm])

page(14, 'GitHub handoff and source navigation')
h('Share the project, not local runtime data')
bullets([
    'Commit source, README, documentation, package manifests and lockfiles. Include this report if desired.',
    'Exclude backend/.env, API keys/passwords, backend/var, local databases, .venv, node_modules and test outputs.',
    'The reviewer creates a new environment file, independent secrets, API key, database and administrator.',
    'Use only clearly synthetic repository fixtures. Do not commit uploaded applicant documents or private screenshots.',
    'Review staged files before pushing. Git ignore rules cannot undo a secret that is already in Git history.',
    'No license file is currently declared; choose an appropriate license before describing the repository as licensed open-source.',
])
h('Where to look in the code')
table(['Path', 'Review focus'], [
    ['backend/app/cli.py', 'Preflight, migrations, secret generation, bootstrap admin and worker commands.'],
    ['backend/app/api/', 'Applicant submissions, staff decisions, access-controlled document routes and admin APIs.'],
    ['backend/app/pipeline/', 'OCR, PII/tokenization, Gemini schema, evidence validation, name/PAN rules.'],
    ['backend/app/models/ and services/', 'Persistent entities, view serialization, identity, policy, notifications and retention.'],
    ['frontend/src/pages/', 'Applicant dashboard and staff overview, queue, review and admin UI.'],
    ['frontend/src/components/', 'Shared stamps, notification bell, previews, uploader and timeline.'],
    ['backend/tests/ and frontend/src/test/', 'Reproducible backend/frontend verification.'],
    ['README.md and docs/DEPENDENCIES.md', 'Primary setup instructions and complete direct package inventory.'],
], [75 * mm, 99 * mm])
p('Rebuild this PDF and the dependency inventory from backend: uv run python ../scripts/build_project_report.py. The generator requires ReportLab, already included in the backend development dependency group.', 'SmallText')

SimpleDocTemplate(str(PDF), pagesize=(210 * mm, 297 * mm), rightMargin=18 * mm, leftMargin=18 * mm,
                  topMargin=18 * mm, bottomMargin=24 * mm,
                  title='Northbridge KYC - Project Report and Local Review Guide',
                  author='Northbridge KYC Project', subject='Features, workflow, models, dependencies and local setup').build(
    story, onFirstPage=footer, onLaterPages=footer)
print(PDF)
