# Feature completion checklist

Legend: **✅ tested** = implemented and covered by an automated test that passed ·
**🟡 built, not verified here** = implemented but not exercised in this environment ·
**❌ not implemented**

## Acceptance tests (spec §17)

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | Registration, login, logout | ✅ tested | `test_auth.py`, E2E |
| 2 | Applicant/staff role separation | ✅ tested | `test_auth.py::test_portal_separation`, E2E |
| 3 | Cross-user and cross-organisation denial | ✅ tested | `test_access.py` |
| 4 | Aadhaar + PAN + one salary slip MATCH | ✅ tested | `test_matching.py` |
| 5 | Aadhaar + PAN + multiple salary slips | ✅ tested | `test_matching.py` |
| 6 | Mandatory PAN; each salary slip must contain matching PAN | ✅ tested | `test_pan_requirements.py`, `test_validation.py` |
| 7 | PAN conflicting with Aadhaar | ✅ tested | `test_matching.py` |
| 8 | One mismatched slip among matching ones | ✅ tested | `test_matching.py` |
| 9 | Missing/unreadable required names → INCOMPLETE | ✅ tested (rules); 🟡 unreadable-image path needs Tesseract | `test_matching.py`, `test_formats_ocr.py` (skipped here) |
| 10 | PDF, JPG/JPEG, PNG for every category | ✅ PDF tested; 🟡 PNG/JPEG written, **skipped** (no Tesseract) | `test_formats_ocr.py` |
| 11 | Scanned PDFs invoke Tesseract | 🟡 written, **skipped** (no Tesseract); missing-Tesseract failure path ✅ tested | `test_formats_ocr.py`, `test_privacy_llm.py` |
| 12 | Oversized, spoofed, corrupt, encrypted files | ✅ tested | `test_validation.py` |
| 13 | Duplicate claims, legitimate resubmission, concurrent submissions | ✅ tested (concurrency on PostgreSQL) | `test_identity.py`, `test_postgres.py` |
| 14 | Same name + different IDs not duplicate | ✅ tested | `test_matching.py` |
| 15 | Stable tokens across documents; isolation across applications | ✅ tested | `test_privacy_llm.py` |
| 16 | Vault cleanup on success, exception, cancellation | ✅ tested | `test_privacy_llm.py` |
| 17 | No fixture PII in outbound requests, logs, progress events | ✅ tested (mocked provider + mocked `google-genai` client) | `test_privacy_llm.py` |
| 18 | Unknown/changed tokens and invalid JSON rejected | ✅ tested | `test_privacy_llm.py` |
| 19 | Gemini timeout/quota never becomes MATCH/MISMATCH | ✅ tested | `test_privacy_llm.py` |
| 20 | MATCH requires human approval | ✅ tested | `test_review.py`, E2E |
| 21 | Approval visible on applicant dashboard | ✅ tested | `test_review.py`, E2E |
| 22 | Rejection and changes-requested workflows | ✅ tested | `test_review.py` |
| 23 | Stale approval prevention after resubmission | ✅ tested | `test_review.py` |
| 24 | SSE reconnect and polling fallback | ✅ tested | `test_jobs_progress.py`, Vitest |
| 25 | Worker restart, retry, idempotency | ✅ tested | `test_jobs_progress.py` |
| 26 | Evidence authorisation, retention and deletion | ✅ tested | `test_access.py`, `test_jobs_progress.py` |
| 27 | PostgreSQL migrations and concurrent identity/review operations | ✅ tested | `test_postgres.py` |
| — | ≥20 synthetic evaluation documents with denominators | ✅ 24 docs; text-PDF set fully evaluated; image/scan set pending OCR | `docs/EVALUATION.md` |
| — | Live Gemini integration test | 🟡 written (`-m live_gemini`), **not run**: no API key available | `test_privacy_llm.py` |

## Product features

| Area | Status |
|---|---|
| Welcome, sign-up, sign-in, staff sign-in, email verification, password reset, dev mailbox | ✅ |
| Applicant dashboard: status, checklist, PAN optionality, drag-and-drop uploads with upload progress, replace/delete, live timeline, per-document results, reviewer feedback, notifications, history, settings | ✅ (E2E covers the main path) |
| Consent acknowledgement before submission | ✅ |
| Draft saving; immutable submitted versions; controlled resubmission | ✅ tested |
| Safe cancellation and reprocessing | ✅ tested |
| Staff dashboard counts, search, filters, pagination, queue age, workload | ✅ (API tested; UI checked manually) |
| Claim/release assignment | ✅ tested (incl. PostgreSQL race) |
| Review screen: masked info, previews with redaction, bounding boxes, extracted fields, OCR evidence, name matrix, slip outcomes, quality and duplicate warnings, timeline, audit trail, applicant vs internal notes | ✅ (checked manually in browser; API parts tested) |
| Approve / reject (reason required) / request changes / internal notes | ✅ tested |
| Admin: staff invites and roles, policy (PAN, slips, size, retention), metrics, identity-conflict resolution | ✅ tested (API) |
| Metadata-only CSV export | ✅ tested |
| Duplicate salary-slip and repeated pay-period detection | ✅ tested |
| Scan-quality suggestions | ✅ tested (synthetic degraded images) |
| Configurable required salary months | ✅ (`required_salary_slips` policy) |
| Revision comparison | 🟡 partial: version history and per-version detail endpoints exist; no side-by-side diff UI |
| Optional local vision-model adapter | 🟡 provider protocol allows it; no vision adapter shipped |
| Image-redaction for hosted image processing (stretch) | ❌ not implemented; images are never sent to Gemini |
| S3-compatible storage adapter | 🟡 implemented, not tested |
| Docker / Compose | 🟡 written, not built here (Docker not installed) |
| OpenTelemetry export | 🟡 wired with an allowlist; exporter not exercised against a collector |
| SMTP email delivery | 🟡 implemented; only dev capture exercised |
| Multiple OCR languages | ❌ English only by default (documented how to add) |
