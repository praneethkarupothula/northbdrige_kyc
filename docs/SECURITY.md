# Security, privacy and data retention

## What the result means

A **MATCH** means the holder names on the submitted documents agree after normalisation, and a Northbridge
reviewer must still approve. It does **not** prove that a document is authentic. Format and checksum checks on
Aadhaar/PAN are local plausibility checks only. This system does not authenticate against UIDAI or Income Tax
Department records, does not make credit or loan decisions, and holds no regulatory certification.

## Authentication and authorisation

* Argon2id password hashing; 12+ characters, three character classes, common-password and name/email checks.
* Server-side sessions (`sessions` table stores SHA-256 of the token); HttpOnly `SameSite=Lax` cookie, `Secure`
  in production; absolute expiry (8 h) and idle expiry (60 min); all sessions revoked on password change/reset.
* CSRF: double-submit cookie `nb_csrf` + `X-CSRF-Token` header, and the token is bound to the session.
* Login rate limiting per keyed email hash and per IP (failures in a 15-minute window).
* Separate applicant and staff sign-in endpoints; the session records its portal and every route checks it.
* Registration always creates `applicant`; a `role` field is rejected. Staff are created by an administrator
  invite (single-use link) or the `bootstrap-admin` CLI. No default passwords.
* Object-level checks return 404 for other users' applications. Staff access is scoped by organisation
  membership (`staff_memberships`).
* Email-verification and password-reset tokens are random, stored hashed, single-use and expiring.

## Data protection

* Uploaded files: validated by magic bytes and real parsing, stored under generated keys (`doc_<uuid>`), encrypted
  with AES-256-GCM (per-purpose HKDF sub-keys, purpose bound as associated data). Original filenames are encrypted.
* Exact extracted values and identity names are encrypted columns. Default views are masked (name initials,
  `XXXX XXXX 1234`, `**/**/YYYY`).
* Identity lookups use HMAC-SHA-256 with `IDENTITY_HMAC_SECRET`; plaintext identifiers are never stored for lookup.
* Document previews are rendered server-side with sensitive regions (IDs, DOB, address, contact, account numbers)
  filled solid black with ≥5 px padding. Unmasked previews, original downloads and field reveals require the
  assigned reviewer or an administrator and are audited. No public or permanent document URLs exist.

## The Gemini boundary

* Only the worker calls Gemini, and only after the local privacy gate approves the payload.
* Before sending, the worker:
  1. detects PII with labels, ID-card layout rules, Indian identifier regexes, and Presidio + spaCy NER;
  2. replaces values with application-scoped tokens;
  3. replaces every word not on a payroll/ID vocabulary allowlist with `[REDACTED]` (OCR text is untrusted
     data; this also neutralises injected instructions);
  4. blocks the payload if any residual Aadhaar-, PAN-, phone-, email-, IFSC- or long-number pattern remains,
     if any vault original appears in it, if a labelled sensitive field is not tokenised, or if unapproved free
     text remains. Blocked documents go to manual review, and nothing is sent.
* A "Presidio found nothing" result is never treated as proof of safety on its own.
* Requests use JSON schema output, temperature 0, no tools and automatic function calling disabled.
* Prompts and responses are never logged; only model name, latency and token counts are recorded.
* Masking reduces what Google receives; it does **not** mean Google retains nothing. Review Google's data terms
  for your account tier before using real data.
* The optional image-redaction stretch goal (sending redacted images to a hosted model) is **not implemented**.
  No images are ever sent to Gemini.

## Telemetry

Logs and spans pass an allowlist (`app/observability/telemetry.py`): only known keys with validated values
(opaque UUIDs, enum-like labels, numbers) survive. Names, filenames, OCR text, prompts, identifiers, credentials
and connection strings are dropped. Validation errors never echo submitted values. Unhandled exceptions are
logged by class name only. Audit records (who did what to which object) are separate and carry only
allowlisted detail keys.

## Retention (configurable product policy)

* Default: originals are kept for review and deleted **7 days after a decision** (`retention_days_after_decision`,
  editable by administrators). This is a prototype product setting, **not a legal retention requirement**. Set it
  according to your own obligations.
* The worker schedules a retention sweep every `RETENTION_SWEEP_MINUTES`; purges are audited.
* Encrypted extracted values, checks, reviews and audit events are retained for the record.
* **Limitations:** deletion removes the file from primary storage only. Database backups, Neon branches/PITR
  history, storage snapshots and object-store versioning keep copies until they expire under their own
  policies. Python cannot guarantee that plaintext bytes are physically zeroised from memory after use.

## Keys

`SESSION_SECRET`, `ENCRYPTION_KEY` and `IDENTITY_HMAC_SECRET` come from the environment (or your secret manager),
never from the database or source. Rotating `ENCRYPTION_KEY` requires re-encrypting stored blobs and columns
(not automated in this release). Rotating `IDENTITY_HMAC_SECRET` invalidates existing fingerprints.

## Known gaps before production

* No MFA for staff; no breached-password (HIBP) check.
* Rate limiting is per-database-window, not a distributed limiter or WAF.
* S3 storage adapter is implemented but not exercised by tests.
* Encryption-key rotation tooling is not implemented.
* No penetration test or external security review has been performed.
