# Architecture

## System overview

```mermaid
flowchart LR
  subgraph Browser
    A[Applicant portal] -->|HttpOnly cookie + CSRF header| P
    S[Staff portal] -->|HttpOnly cookie + CSRF header| P
  end
  P[Vite dev proxy / nginx<br/>same origin] --> API[FastAPI API]
  API -->|SQLAlchemy| DB[(PostgreSQL / Neon<br/>one DB per environment)]
  API -->|encrypted blobs, opaque keys| ST[(Private storage<br/>local dir or S3-compatible)]
  W[Worker<br/>python -m app.cli worker] -->|claim jobs<br/>FOR UPDATE SKIP LOCKED| DB
  W --> ST
  W -->|masked, minimised text only| G[Google Gemini<br/>google-genai SDK]
  API -. SSE progress / polling .-> A
  API -. SSE progress / polling .-> S
```

* The **API** never processes documents inline. Submission enqueues a durable job (`processing_jobs`).
* The **worker** owns all raw-document handling: decrypting the file, OCR, PII detection and the Gemini call.
  Raw text and the token vault exist only in worker memory for one processing attempt.
* The browser talks to one origin; the API is proxied, so the session cookie stays first-party (`SameSite=Lax`)
  and CORS stays restricted to `ALLOWED_ORIGINS`.

## Processing pipeline (one application version attempt)

```mermaid
flowchart TD
  U[1 Upload received<br/>magic bytes + parse, encrypted at rest] --> V[2 File validation<br/>re-check stored bytes]
  V --> Q[3 Quality inspection<br/>blur, brightness, contrast, skew, DPI, EXIF]
  Q --> T{4 Text layer adequate?}
  T -->|yes| PT[PyMuPDF words + coordinates<br/>no invented confidence]
  T -->|no / image| OCR[Render locally, deskew, Tesseract<br/>bounded re-OCR fallback]
  PT --> M
  OCR -->|still unreadable| NR[NEEDS_REUPLOAD]
  OCR --> M[5 PII detection + masking<br/>labels, layout, regex, Presidio + spaCy<br/>shared TokenVault per attempt]
  M --> GATE{Outbound privacy gate<br/>residual identifiers? vault values?<br/>unapproved free text?}
  GATE -->|blocked| MR[Manual review<br/>nothing sent]
  GATE -->|approved| L[6 Gemini structured extraction<br/>JSON schema, temperature 0, no tools]
  L -->|timeout / quota / 5xx| FAIL[Retry with backoff -> FAILED<br/>never MATCH or MISMATCH]
  L --> SV[7 Schema validation<br/>known tokens, right types, line evidence]
  SV -->|invalid after bounded retries| MR
  SV --> D[8 Duplicate identity check<br/>HMAC fingerprints, advisory lock]
  D --> N[9 Name consistency rules<br/>exact token equality in Python]
  N --> R[10 Awaiting human review]
  R --> X[11 Approved / rejected / changes requested<br/>by a Northbridge reviewer]
```

Every numbered stage writes a `processing_events` row (ID, version ID, attempt, stage, status, fixed message).
The browser receives them over authenticated SSE (resumable with `Last-Event-ID`) or by polling, so a refresh
reloads the full history.

## Token vault and the Gemini boundary

```mermaid
sequenceDiagram
  participant W as Worker
  participant V as TokenVault (memory, per attempt)
  participant G as Gemini
  W->>V: "Asha Verma" (Aadhaar) -> [[PERSON_1]]
  W->>V: "ASHA  verma" (salary slip) -> [[PERSON_1]] (same normalised name)
  W->>V: "Ramesh Verma" (PAN, father) -> [[PERSON_2]]
  W->>W: privacy gate on "L003 [p1]: Name: [[PERSON_1]]" ...
  W->>G: masked lines + schema (never the vault)
  G-->>W: {"full_name": "[[PERSON_1]]", "full_name_line": 3, ...}
  W->>W: reject unknown/altered tokens, require line evidence
  W->>V: resolve tokens locally -> encrypt exact values for review
  W->>W: compare tokens: [[PERSON_1]] == [[PERSON_1]] -> MATCH
  Note over W,V: finally: vault.clear() on success, failure and cancellation
```

Gemini classifies the document and copies tokens into the schema. It never sees raw text, full identifiers or
the mapping, and it never decides whether names match or whether to approve.

## Status model (three independent fields)

| Field | Values | Set by |
|---|---|---|
| `processing_status` | NOT_STARTED, QUEUED, RUNNING, SUCCEEDED, FAILED, NEEDS_REUPLOAD | worker |
| `check_status` | MATCH, MISMATCH, INCOMPLETE (per-check also NOT_APPLICABLE) | deterministic rules |
| `review_status` | NOT_READY, PENDING_REVIEW, APPROVED, REJECTED, CHANGES_REQUESTED | reviewer |

A processing failure leaves `check_status` empty. Approval requires `SUCCEEDED + MATCH + PENDING_REVIEW`, the
current version, the expected `row_version`, and no unresolved identity conflicts. A partial unique index allows
only one final decision per version.

## Versioning and resubmission

Submitted versions are immutable. "Start a correction" creates version *n+1* (documents re-encrypted under new
keys), marks *n* as `SUPERSEDED` and not current, and all approval paths reject non-current versions or stale
`row_version` values.

## Identity duplicates

```mermaid
flowchart LR
  X[canonical Aadhaar digits / PAN uppercase] --> H[HMAC-SHA-256 secret, TYPE:value]
  H --> L{lock fingerprint}
  L --> C{confirmed for another account?}
  C -->|yes| M1[MISMATCH: generic message to applicant]
  C -->|no| O{unverified claims by others?}
  O -->|yes| M2[CONFLICT on all claims -> admin resolution]
  O -->|no| OK[UNVERIFIED claim]
  OK -->|reviewer approval| CF[confirmed_identities unique fingerprint]
```

OCR-uncertain or format-implausible identifiers create `UNTRUSTED` claims that reserve nothing, so a bad first
upload cannot lock out the real holder. Names never determine duplication.

## Data classes

| Class | Examples | Where | Lifetime |
|---|---|---|---|
| Transient processing | raw OCR text, token vault, decrypted bytes | worker memory | one attempt (references dropped in `finally`; physical memory zeroisation is not guaranteed by Python) |
| Review data | original files, exact extracted values, identity names | encrypted blobs; `*_enc` columns (AES-256-GCM) | originals purged N days after decision (configurable); encrypted values kept for audit |
| Telemetry | stage, status, durations, error class, counts | logs / OTel spans | allowlisted keys only |

## Repository layout

```
backend/app/api            HTTP routes (auth, applications, documents, staff/admin)
backend/app/auth           passwords, sessions, authorisation dependencies
backend/app/models         SQLAlchemy models and enums
backend/app/schemas        Pydantic request/response models
backend/app/services       audit, identity, notifications, retention, status text, views
backend/app/pipeline       validation, quality, OCR, PII/vault/gate, LLM providers, output validation, matching
backend/app/workers        durable queue and worker runner
backend/app/storage        encrypted blob storage adapters
backend/app/observability  allowlisted logging and tracing
backend/alembic            migrations (0001 schema, 0002 seed organisation)
backend/tests              pytest suite
frontend/                  React + TypeScript + Vite + Tailwind + shadcn/ui
tests/e2e                  Playwright journey
scripts/                   samples, evaluation, E2E helpers
samples/                   synthetic documents + manifest
docs/                      this documentation, OpenAPI export, evaluation results
```
