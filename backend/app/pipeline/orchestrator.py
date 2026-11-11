"""Processing of one application version attempt.

All documents of the attempt share one TokenVault (so the same name on Aadhaar and on a
salary slip maps to the same token). The vault is cleared in ``finally`` on success,
failure and cancellation. Progress is persisted as ProcessingEvents after every stage.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt_json
from app.models import (
    Application,
    ApplicationVersion,
    CheckStatus,
    Document,
    DocumentCategory,
    ExtractionResult,
    ProcessingStatus,
    ReviewStatus,
    Stage,
    User,
    VerificationCheck,
    VersionState,
)
from app.observability.telemetry import filter_attributes, get_logger, span
from app.pipeline import identifiers as ids
from app.pipeline.llm import (
    ExtractionProvider, ProviderError, ProviderSchemaError, SystemDependencyMissing, get_provider,
)
from app.pipeline.matching import DocOutcome, evaluate
from app.pipeline.ocr import OcrUnavailable, extract_text, inspect_document
from app.pipeline.ocr_schema import OcrResultSchema
from app.pipeline.output_validation import OutputRejected, validate_output
from app.pipeline.pii import TokenVault, privacy_gate, sanitize
from app.pipeline.validation import FileRejected, validate_upload
from app.services.events import emit
from app.services.duplicate_policy import earlier_reuse, reject_duplicate
from app.services.identity import GENERIC_DUPLICATE_MESSAGE, IdentityInput, register_claim
from app.services.notify import notify
from app.services.policy import get_policy
from app.services.status import applicant_status
from app.storage.blob import get_store

log = get_logger()

CATEGORY_LABEL = {"AADHAAR": "Aadhaar", "PAN": "PAN card", "SALARY_SLIP": "Salary slip", "BANK_STATEMENT": "Bank statement"}

# Hook used by tests to observe the vault lifecycle; production code never reads it.
LAST_VAULT: list[TokenVault] = []


class JobCancelled(Exception):
    pass


def _label(doc: Document, docs: list[Document]) -> str:
    base = CATEGORY_LABEL.get(doc.category, doc.category)
    same = [d for d in docs if d.category == doc.category]
    return f"{base} {same.index(doc) + 1}" if len(same) > 1 else base


def _masked_display(field: str, value: str | None, id_type: str | None = None) -> str | None:
    if value is None:
        return None
    if field == "full_name":
        return ids.mask_name(value)
    if field == "date_of_birth":
        return "**/**/" + value[-4:] if len(value) >= 4 and value[-4:].isdigit() else "****"
    if field == "pan_number":
        return ids.mask_pan(ids.canonical_pan(value))
    if field == "id_number":
        return ids.mask_pan(ids.canonical_pan(value)) if id_type == "PAN" else ids.mask_aadhaar(ids.canonical_aadhaar(value))
    if field == "employer_name":
        return value[:1] + "***"
    return "***"


def process_version(
    db: Session,
    version_id: str,
    attempt: int,
    *,
    cancel_check: Callable[[], bool] = lambda: False,
    provider: ExtractionProvider | None = None,
) -> str:
    version = db.get(ApplicationVersion, version_id)
    if version is None or not version.is_current or version.state != VersionState.SUBMITTED:
        return "SKIPPED_NOT_CURRENT"
    if version.review_status in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
        return "SKIPPED_FINAL_DECISION"
    if version.processing_attempt != attempt:
        return "SKIPPED_SUPERSEDED_ATTEMPT"
    app = db.get(Application, version.application_id)
    applicant = db.get(User, app.applicant_id)
    policy = get_policy(db, app.organization_id)
    docs = [d for d in version.documents if d.purged_at is None and d.storage_key]

    # idempotent re-run of the same attempt: remove partial results from a previous try
    doc_ids = [d.id for d in docs]
    if doc_ids:
        db.execute(delete(ExtractionResult).where(ExtractionResult.document_id.in_(doc_ids),
                                                  ExtractionResult.attempt == attempt))
    db.execute(delete(VerificationCheck).where(VerificationCheck.version_id == version.id,
                                               VerificationCheck.attempt == attempt))
    version.processing_status = ProcessingStatus.RUNNING
    version.check_status = None
    version.review_status = ReviewStatus.NOT_READY
    version.failure_code = None
    for d in docs:
        d.processing_status, d.current_stage, d.error_code, d.error_message = ProcessingStatus.QUEUED, None, None, None
    emit(db, version, Stage.UPLOAD_RECEIVED, "info", f"Processing started for {len(docs)} document(s).")

    provider = provider or get_provider()
    vault = TokenVault(scope=f"{version.id}:{attempt}")
    LAST_VAULT[:] = [vault]

    def check_cancel():
        if cancel_check():
            raise JobCancelled()

    try:
        outcomes: list[DocOutcome] = []
        identity_inputs: list[IdentityInput] = []
        for doc in docs:
            check_cancel()
            out, ident = _process_document(db, version, doc, docs, vault, provider, check_cancel)
            outcomes.append(out)
            if ident:
                identity_inputs.append(ident)

        check_cancel()
        # ---------------------------------------------------------------- duplicates
        emit(db, version, Stage.DUPLICATE_CHECK, "started", "Checking identity numbers against existing records.")
        id_checks: list[VerificationCheck] = []
        for item in identity_inputs:
            res = register_claim(db, user_id=applicant.id, application_id=app.id, version_id=version.id, item=item)
            id_checks.append(VerificationCheck(
                version_id=version.id, attempt=attempt, check_type=f"DUPLICATE_IDENTITY_{item.id_type}",
                document_id=item.document_id, status=res.status, code=res.code,
                detail={"masked": res.masked, "claim_status": res.claim_status},
            ))
        db.add_all(id_checks)
        emit(db, version, Stage.DUPLICATE_CHECK, "succeeded", "Identity duplicate check complete.")

        # ---------------------------------------------------------------- name rules
        check_cancel()
        emit(db, version, Stage.NAME_CHECKS, "started", "Comparing names across documents.")
        checks, aggregate_status = evaluate(
            outcomes, pan_status=version.pan_status, pan_policy=policy.pan_policy,
            required_salary_slips=policy.required_salary_slips,
            account_name_norm=ids.normalize_name(applicant.full_name),
        )
        for c in checks:
            db.add(VerificationCheck(version_id=version.id, attempt=attempt, check_type=c.check_type,
                                     document_id=c.document_id, reference_document_id=c.reference_document_id,
                                     status=c.status, code=c.code, detail=c.detail))
        emit(db, version, Stage.NAME_CHECKS, "succeeded", f"Automated consistency result: {aggregate_status}.")

        # ---------------------------------------------------------------- finish
        needs_reupload = any(d.processing_status == ProcessingStatus.NEEDS_REUPLOAD for d in docs)
        version.processing_status = ProcessingStatus.NEEDS_REUPLOAD if needs_reupload else ProcessingStatus.SUCCEEDED
        version.check_status = aggregate_status
        version.review_status = ReviewStatus.PENDING_REVIEW
        version.processed_at = datetime.now(UTC)
        version.row_version += 1
        duplicates = earlier_reuse(db, version)
        if duplicates:
            reject_duplicate(db, version, duplicates)
        else:
            emit(db, version, Stage.AWAITING_REVIEW, "info", "Waiting for a Northbridge reviewer.", commit=False)
        st = applicant_status(version, has_identity_conflict=any(
            c.code in ("IDENTITY_HELD_BY_ANOTHER_ACCOUNT", "POTENTIAL_DUPLICATE_IDENTITY") for c in id_checks))
        if not duplicates:
            notify(db, applicant.id, "PROCESSING_COMPLETE", st["headline"], st["detail"], app.id)
        db.commit()
        log.info("version_processed", version_id=version.id, check_status=aggregate_status, attempt=attempt)
        return aggregate_status
    except JobCancelled:
        db.rollback()
        version = db.get(ApplicationVersion, version_id)
        version.state = VersionState.DRAFT
        version.processing_status = ProcessingStatus.NOT_STARTED
        version.check_status = None
        version.review_status = ReviewStatus.NOT_READY
        version.row_version += 1
        for d in version.documents:
            d.processing_status, d.current_stage = ProcessingStatus.NOT_STARTED, None
        emit(db, version, Stage.UPLOAD_RECEIVED, "info", "Processing was cancelled. The application is a draft again.")
        raise
    finally:
        vault.clear()


def _process_document(db, version, doc, docs, vault, provider, check_cancel):
    s = get_settings()
    label = _label(doc, docs)
    store = get_store()

    def stage(st: Stage, status: str, msg: str):
        doc.current_stage = st
        emit(db, version, st, status, f"{label}: {msg}", document_id=doc.id)

    def fail(code: str, msg: str, proc_status=ProcessingStatus.FAILED) -> tuple[DocOutcome, None]:
        doc.processing_status, doc.error_code, doc.error_message = proc_status, code, msg
        stage(doc.current_stage or Stage.FILE_VALIDATION, "failed", msg)
        return DocOutcome(doc.id, doc.category, usable=False, reason=code, content_fp=doc.content_fingerprint), None

    doc.processing_status = ProcessingStatus.RUNNING
    first_page_only = doc.category == DocumentCategory.BANK_STATEMENT

    # 2. file validation -----------------------------------------------------------
    stage(Stage.FILE_VALIDATION, "started", "validating file")
    data = store.get(doc.storage_key)
    try:
        validate_upload(data)
    except FileRejected as fr:
        return fail(fr.code, fr.message, ProcessingStatus.NEEDS_REUPLOAD)
    stage(Stage.FILE_VALIDATION, "succeeded", f"{doc.file_format.upper()} with {doc.page_count} page(s) is valid")
    check_cancel()

    # 3. quality ---------------------------------------------------------------------
    stage(Stage.QUALITY_INSPECTION, "started", "inspecting scan quality")
    quality = inspect_document(data, doc.file_format, first_page_only=first_page_only)
    doc.quality = quality
    msg = "no quality issues found" if not quality["flags"] else "warnings: " + ", ".join(quality["flags"]).lower()
    stage(Stage.QUALITY_INSPECTION, "succeeded", msg)
    check_cancel()

    # 4. text extraction ---------------------------------------------------------------
    stage(Stage.TEXT_EXTRACTION, "started", "extracting text locally")
    t0 = time.monotonic()
    with span("text_extraction", document_id=doc.id, file_format=doc.file_format):
        try:
            text = extract_text(data, doc.file_format, first_page_only=first_page_only)
        except OcrUnavailable:
            raise SystemDependencyMissing("tesseract missing") from None
        except TimeoutError:
            return fail("OCR_TIMEOUT", "text recognition timed out; please upload a clearer copy",
                        ProcessingStatus.NEEDS_REUPLOAD)
    doc.pages_processed = text.pages_processed
    pages_note = f"pages processed: {', '.join(map(str, text.pages_processed))} of {text.pages_total}"
    if first_page_only and text.pages_total > 1:
        pages_note += " (bank statements: first page only in this release)"
    log.info("text_extracted", document_id=doc.id, extraction_method=text.method, ocr_confidence=text.mean_conf or 0,
             page_count=len(text.pages), duration_ms=int((time.monotonic() - t0) * 1000))
    if not text.readable:
        return fail("UNREADABLE", "the text could not be read reliably after local re-processing; "
                    "please upload a clearer scan", ProcessingStatus.NEEDS_REUPLOAD)
    conf = f", mean OCR confidence {text.mean_conf}%" if text.mean_conf is not None else " (embedded PDF text)"
    stage(Stage.TEXT_EXTRACTION, "succeeded", f"{text.method}{conf}; {pages_note}")
    check_cancel()

    stage(Stage.OCR_SCHEMA_VALIDATION, "started", "validating extracted pages, lines, words, coordinates and confidence with Pydantic")
    try:
        with span("ocr_schema_validation", document_id=doc.id):
            schema = OcrResultSchema.model_validate(text)
        line_count = sum(len(page.lines) for page in schema.pages)
        stage(Stage.OCR_SCHEMA_VALIDATION, "succeeded",
              f"Pydantic schema valid: {len(schema.pages)} page(s), {line_count} line(s); page references, word bounds and confidence checked locally")
        del schema
    except ValidationError:
        # Never persist ValidationError details: they can contain input text.
        return fail("OCR_SCHEMA_INVALID", "extracted text did not pass the Pydantic OCR schema; manual review is required")
    check_cancel()

    # 5. PII masking + privacy gate -----------------------------------------------------
    stage(Stage.PII_MASKING, "started", "detecting and masking personal data")
    sanitized = sanitize(text.lines, doc.category, vault)
    payload = sanitized.payload(s.llm_max_input_chars)
    doc.quality = {**quality, "redactions": sanitized.redaction_boxes}
    gate = privacy_gate(sanitized, doc.category, vault, payload)
    if not gate.approved:
        log.warning("privacy_gate_blocked", document_id=doc.id, count=len(gate.reasons))
        return fail("PRIVACY_GATE_BLOCKED", "withheld from automated extraction for privacy; a Northbridge "
                    "reviewer will examine this document manually")
    stage(Stage.PII_MASKING, "succeeded",
          f"masked {sum(sanitized.entity_counts.values())} item(s); privacy gate approved the payload")
    text_method, text_conf = text.method, text.mean_conf
    del text  # raw text is transient; drop the reference
    check_cancel()

    # 6-7. LLM extraction + schema validation (bounded retries) -------------------------
    stage(Stage.LLM_EXTRACTION, "started", "requesting structured extraction from masked text")
    validated = None
    call = None
    last_reason = "LLM_SCHEMA_INVALID"
    response_received = False
    for attempt_no in range(s.gemini_max_retries + 1):
        try:
            with span("llm_extract", document_id=doc.id, provider=provider.name, attempt=attempt_no):
                call = _call_with_transport_retry(provider, payload, doc.category, s.gemini_max_retries)
        except ProviderSchemaError:
            last_reason = "LLM_SCHEMA_INVALID"
            continue
        stage(Stage.LLM_EXTRACTION, "succeeded", f"structured response received ({call.latency_ms} ms)")
        stage(Stage.LLM_TELEMETRY, "started", "recording privacy-safe model-call measurements")
        metrics = filter_attributes({"model": call.model, "latency_ms": call.latency_ms,
                                     "input_tokens": call.input_tokens, "output_tokens": call.output_tokens})
        log.info("llm_call_telemetry", document_id=doc.id, retry_count=attempt_no, **metrics)
        usage = lambda key: str(metrics[key]) if key in metrics else "unavailable"
        stage(Stage.LLM_TELEMETRY, "succeeded",
              f"Call {attempt_no + 1}: model={metrics.get('model', 'unavailable')}; latency={usage('latency_ms')} ms; input tokens={usage('input_tokens')}; output tokens={usage('output_tokens')}. No document text recorded.")
        if not response_received:
            stage(Stage.SCHEMA_VALIDATION, "started", "validating response against the schema and evidence")
            response_received = True
        try:
            validated = validate_output(call.extraction, sanitized, vault, payload)
            break
        except OutputRejected as rej:
            last_reason = rej.reason.split(":")[0]
            log.warning("llm_output_rejected", document_id=doc.id, error_class=last_reason, retry_count=attempt_no)
    if validated is None:
        if not response_received:
            doc.current_stage = Stage.LLM_EXTRACTION
            return fail("EXTRACTION_INVALID", "Gemini did not return a valid structured response after retries; "
                        "routed to manual review")
        doc.current_stage = Stage.SCHEMA_VALIDATION
        return fail("EXTRACTION_INVALID", "the automated extraction did not pass validation; routed to manual review")
    log.info("llm_extracted", document_id=doc.id, model=call.model, latency_ms=call.latency_ms,
             input_tokens=call.input_tokens or 0, output_tokens=call.output_tokens or 0)

    # resolve tokens locally (vault never leaves this process)
    exact = {f: (vault.original(t) if t else None) for f, t in validated.tokens.items()}
    id_type = None
    if validated.tokens.get("id_number"):
        id_type = vault.type_of(validated.tokens["id_number"])
    display = {f: _masked_display(f, v, id_type) for f, v in exact.items()}
    display.update({k: (str(v) if v is not None else None) for k, v in validated.money.items()})
    net, gross = validated.money.get("net_salary"), validated.money.get("gross_salary")
    flags = list(quality["flags"])
    if validated.document_type != doc.category:
        flags.append("DOCUMENT_TYPE_DIFFERS_FROM_UPLOAD_SLOT")
    db.add(ExtractionResult(
        document_id=doc.id, attempt=version.processing_attempt, document_type=validated.document_type,
        fields_enc=encrypt_json(exact, "extraction-fields"), display_fields=display,
        provenance={"fields": validated.evidence, "redactions": sanitized.redaction_boxes},
        pay_period_month=validated.pay_period_month, pay_period_year=validated.pay_period_year,
        gross_salary=gross, net_salary=net, monthly_salary=net or gross, currency=validated.currency,
        missing_fields=validated.missing_fields, quality_flags=flags,
        extraction_method=text_method, ocr_confidence=text_conf,
        model_name=call.model, llm_latency_ms=call.latency_ms, entity_counts=sanitized.entity_counts,
    ))
    doc.processing_status = ProcessingStatus.SUCCEEDED
    stage(Stage.SCHEMA_VALIDATION, "succeeded", "fields validated with line-level evidence")

    name = exact.get("full_name")
    outcome = DocOutcome(
        doc.id, doc.category, usable=True, document_type=validated.document_type,
        name_token=validated.tokens.get("full_name"), name_norm=ids.normalize_name(name) if name else None,
        name_from_ocr=text_method != "pdf_text",
        pan_token=validated.tokens.get("pan_number") if doc.category == DocumentCategory.SALARY_SLIP
            else validated.tokens.get("id_number") if doc.category == DocumentCategory.PAN else None,
        employer_token=validated.tokens.get("employer_name"),
        pay_period=(validated.pay_period_month, validated.pay_period_year) if validated.pay_period_month else None,
        net_salary=net, gross_salary=gross, content_fp=doc.content_fingerprint, page_count=doc.page_count,
    )
    ident = None
    if doc.category in (DocumentCategory.AADHAAR, DocumentCategory.PAN) and validated.document_type == doc.category:
        tok = validated.tokens.get("id_number")
        expected = "AADHAAR" if doc.category == DocumentCategory.AADHAAR else "PAN"
        if tok and vault.type_of(tok) == expected:
            raw = vault.original(tok)
            canon = ids.canonical_aadhaar(raw) if expected == "AADHAAR" else ids.canonical_pan(raw)
            ok, _ = ids.aadhaar_plausible(canon) if expected == "AADHAAR" else ids.pan_plausible(canon)
            masked = ids.mask_aadhaar(canon) if expected == "AADHAAR" else ids.mask_pan(canon)
            ident = IdentityInput(expected, canon, ok, vault.min_conf(tok), masked, name, doc.id)
        else:
            ident = IdentityInput(expected, None, False, None, "not extracted", name, doc.id)
    return outcome, ident


def _call_with_transport_retry(provider, payload, category, retries):
    delay = 1.0
    for i in range(retries + 1):
        try:
            return provider.extract(payload, category)
        except ProviderError as exc:
            if not exc.retryable or i == retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def mark_failed(db: Session, version_id: str, code: str, *, will_retry: bool) -> None:
    """Processing failure is never converted into MATCH or MISMATCH."""
    version = db.get(ApplicationVersion, version_id)
    if version is None:
        return
    app = db.get(Application, version.application_id)
    version.check_status = None
    version.review_status = ReviewStatus.NOT_READY
    version.failure_code = code
    if will_retry:
        version.processing_status = ProcessingStatus.QUEUED
        emit(db, version, Stage.LLM_EXTRACTION, "failed",
             "A temporary processing error occurred. A retry has been scheduled automatically.")
    else:
        version.processing_status = ProcessingStatus.FAILED
        version.row_version += 1
        emit(db, version, Stage.LLM_EXTRACTION, "failed",
             "Processing could not be completed. This is a system error, not a problem with your documents.",
             commit=False)
        notify(db, app.applicant_id, "PROCESSING_FAILED", "Processing could not be completed",
               "We could not finish processing because of a system error. This is not a document mismatch. "
               "Northbridge staff have been alerted and will retry.", app.id)
        db.commit()


__all__ = ["process_version", "mark_failed", "JobCancelled", "GENERIC_DUPLICATE_MESSAGE"]
