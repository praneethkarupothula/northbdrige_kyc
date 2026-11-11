"""Serialisers shared by applicant and staff routes. Default views are masked."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    AuditEvent,
    ApplicationVersion,
    CheckStatus,
    Document,
    IdentityClaim,
    ExtractionResult,
    OrgPolicy,
    ProcessingEvent,
    STAGE_LABELS,
    Stage,
    VerificationCheck,
)
from app.pipeline.quality import SUGGESTIONS
from app.services.status import applicant_status

CATEGORY_LABEL = {"AADHAAR": "Aadhaar", "PAN": "PAN card", "SALARY_SLIP": "Salary slip", "BANK_STATEMENT": "Bank statement"}

EXPLANATIONS = {
    "DOCUMENTS_ALREADY_USED": "These documents or identifiers have already been used for another account. This submission was automatically rejected.",
    "NAME_OCR_UNCERTAIN": "The scanned names differ only in characters OCR can confuse (such as I and l). A clearer image or manual review is needed; this is not a confirmed name mismatch.",
    "PAN_NAME_UNAVAILABLE": "The PAN card holder’s name could not be confirmed.",
    "PAN_NAME_DIFFERS": "The employee name on this salary slip differs from the name on the PAN card.",
    "SALARY_PAN_NOT_EXTRACTED": "A PAN number must be printed and readable on every salary slip.",
    "PAN_NUMBER_UNAVAILABLE": "The PAN number on the uploaded PAN card could not be confirmed.",
    "SALARY_PAN_AGREES": "The PAN number on this salary slip matches the uploaded PAN card.",
    "SALARY_PAN_DIFFERS": "The PAN number on this salary slip differs from the uploaded PAN card.",
    "FATHER_NAMES_AGREE": "The father’s names (S/O) on these documents agree.",
    "FATHER_NAMES_DIFFER": "The father’s name (S/O) differs between these documents.",
    "FATHER_NAME_NOT_AVAILABLE": "Father’s name (S/O) comparison is unavailable because it was not explicitly provided on both documents.",
    "NAMES_IDENTICAL_AFTER_NORMALISATION": "The names are identical after normalising case, spacing and punctuation.",
    "NAMES_DIFFER": "The name on this document differs from the name on the Aadhaar card.",
    "NAME_EXTRACTED": "The holder's name was read from the Aadhaar card.",
    "NAME_NOT_EXTRACTED": "No holder name could be read from this document.",
    "UNREADABLE": "The document could not be read clearly.",
    "OCR_UNAVAILABLE": "The OCR engine is not installed on the server (system error, not a document problem).",
    "PRIVACY_GATE_BLOCKED": "Automated extraction was withheld for privacy; manual review is required.",
    "EXTRACTION_INVALID": "Automated extraction did not pass validation; manual review is required.",
    "DOCUMENT_TYPE_DIFFERS_FROM_UPLOAD_SLOT": "This file does not look like the document type it was uploaded as.",
    "PRIMARY_NAME_UNAVAILABLE": "Cannot compare because the Aadhaar name is unavailable.",
    "AADHAAR_MISSING": "An Aadhaar document is required.",
    "PAN_REQUIRED_BY_POLICY": "The current policy requires a PAN card.",
    "PAN_UNAVAILABLE": "The applicant stated they do not have a PAN card (allowed by the current prototype policy).",
    "PAN_NOT_REQUESTED": "PAN is not requested under the current policy.",
    "SALARY_SLIPS_BELOW_REQUIRED": "Fewer readable salary slips than the policy requires.",
    "IDENTICAL_FILE_REPEATED": "This file is identical to another uploaded salary slip and was counted once.",
    "DUPLICATE_PAY_PERIOD": "Another salary slip covers the same pay period; counted once.",
    "SALARY_SUMMARY": "Salary information for review (not a credit assessment).",
    "EMPLOYER_DIFFERS": "Salary slips show more than one employer. Review information only.",
    "BANK_STATEMENT_INFORMATIONAL": "Bank statements are informational and do not replace salary slips.",
    "ACCOUNT_NAME_AGREES": "The name entered at registration agrees with the Aadhaar name.",
    "ACCOUNT_NAME_DIFFERS": "The name entered at registration differs from the Aadhaar name (documents take precedence).",
    "NO_CONFLICTING_CLAIM": "No other account claims this identifier.",
    "IDENTITY_CONFIRMED_FOR_THIS_ACCOUNT": "This identifier was previously confirmed for this account by staff.",
    "IDENTITY_HELD_BY_ANOTHER_ACCOUNT": "This identifier is confirmed for a different account.",
    "POTENTIAL_DUPLICATE_IDENTITY": "Another account has submitted the same identifier. Staff review required.",
    "CLAIM_PREVIOUSLY_REJECTED_BY_STAFF": "Staff previously rejected this account's claim to the identifier.",
    "IDENTIFIER_OCR_UNCERTAIN": "The identifier was read with low OCR confidence and was not reserved.",
    "IDENTIFIER_FORMAT_IMPLAUSIBLE": "The identifier failed the local format plausibility check and was not reserved.",
    "IDENTIFIER_NOT_EXTRACTED": "No identifier could be read from this document.",
}


def explain(code: str) -> str:
    return EXPLANATIONS.get(code, code.replace("_", " ").capitalize() + ".")


def latest_results(db: Session, version: ApplicationVersion) -> dict[str, ExtractionResult]:
    ids = [d.id for d in version.documents]
    if not ids:
        return {}
    rows = db.scalars(select(ExtractionResult).where(ExtractionResult.document_id.in_(ids),
                                                     ExtractionResult.attempt == version.processing_attempt)).all()
    return {r.document_id: r for r in rows}


def version_checks(db: Session, version: ApplicationVersion) -> list[VerificationCheck]:
    return list(db.scalars(select(VerificationCheck).where(
        VerificationCheck.version_id == version.id, VerificationCheck.attempt == version.processing_attempt,
    ).order_by(VerificationCheck.created_at)))


def _doc_attention(doc: Document, checks: list[VerificationCheck], staff: bool) -> str | None:
    if doc.processing_status == "NEEDS_REUPLOAD":
        return doc.error_message or "Please upload a clearer copy of this document."
    for c in checks:
        if c.document_id == doc.id and c.status == CheckStatus.MISMATCH:
            if c.check_type.startswith("DUPLICATE_IDENTITY") and not staff:
                return "We need to check the identity details on this document. Please contact support."
            if c.code == "NAMES_DIFFER":
                return "The name on this document does not match the name on your Aadhaar card."
            if c.code == "DOCUMENT_TYPE_DIFFERS_FROM_UPLOAD_SLOT":
                return "This file does not look like the document type it was uploaded as."
    if doc.processing_status == "FAILED" and doc.error_code in ("PRIVACY_GATE_BLOCKED", "EXTRACTION_INVALID"):
        return "A Northbridge reviewer will check this document manually."
    return None


def serialize_document(doc: Document, result: ExtractionResult | None, checks, *, staff: bool, dup_of=None) -> dict:
    q = doc.quality or {}
    flags = q.get("flags", [])
    extraction = None
    if result is not None:
        extraction = {
            "document_type": result.document_type,
            "fields": result.display_fields,  # masked by default
            "pay_period": (f"{result.pay_period_year}-{result.pay_period_month:02d}"
                           if result.pay_period_month and result.pay_period_year else None),
            "gross_salary": str(result.gross_salary) if result.gross_salary is not None else None,
            "net_salary": str(result.net_salary) if result.net_salary is not None else None,
            "monthly_salary": str(result.monthly_salary) if result.monthly_salary is not None else None,
            "currency": result.currency,
            "missing_fields": result.missing_fields,
            "quality_flags": result.quality_flags,
            "extraction_method": result.extraction_method,
            "ocr_confidence": float(result.ocr_confidence) if result.ocr_confidence is not None else None,
        }
        if staff:
            extraction.update({
                "provenance": result.provenance.get("fields", {}),
                "model_name": result.model_name,
                "llm_latency_ms": result.llm_latency_ms,
                "entity_counts": result.entity_counts,
            })
    return {
        "id": doc.id, "category": doc.category, "file_format": doc.file_format, "size_bytes": doc.size_bytes,
        "page_count": doc.page_count, "pages_processed": doc.pages_processed,
        "processing_status": doc.processing_status, "current_stage": doc.current_stage,
        "error_code": doc.error_code, "error_message": doc.error_message,
        "quality_flags": flags, "quality_suggestions": [SUGGESTIONS[f] for f in flags if f in SUGGESTIONS],
        "uploaded_at": doc.uploaded_at, "purged": doc.purged_at is not None, "duplicate_of": dup_of,
        "extraction": extraction, "attention": _doc_attention(doc, checks, staff),
    }


def serialize_check(c: VerificationCheck, *, staff: bool) -> dict:
    detail = dict(c.detail or {})
    if not staff:
        detail.pop("claim_status", None)
        detail.pop("hints", None)
        detail.pop("similarity", None)
    explanation = explain(c.code)
    if not staff and c.code in ("IDENTITY_HELD_BY_ANOTHER_ACCOUNT", "POTENTIAL_DUPLICATE_IDENTITY",
                                "CLAIM_PREVIOUSLY_REJECTED_BY_STAFF"):
        from app.services.identity import GENERIC_DUPLICATE_MESSAGE
        explanation = GENERIC_DUPLICATE_MESSAGE
    status = c.status
    if c.check_type.startswith("DUPLICATE_IDENTITY") and (c.detail or {}).get("claim_status") == "UNTRUSTED":
        status = CheckStatus.NOT_APPLICABLE
        explanation += " This warning does not affect document name or PAN-number matching; the identifier remains unvalidated."
    return {"id": c.id, "check_type": c.check_type, "status": status, "code": c.code, "document_id": c.document_id,
            "reference_document_id": c.reference_document_id, "detail": detail, "explanation": explanation}


def serialize_version(db: Session, v: ApplicationVersion, *, staff: bool) -> dict:
    results = latest_results(db, v)
    checks = version_checks(db, v) if v.state != "DRAFT" else []
    seen: dict[str, str] = {}
    docs = []
    for d in v.documents:
        dup = seen.get(d.content_fingerprint)
        seen.setdefault(d.content_fingerprint, d.id)
        docs.append(serialize_document(d, results.get(d.id), checks, staff=staff, dup_of=dup))
    conflict = bool(db.scalar(select(IdentityClaim.id).where(IdentityClaim.version_id == v.id,
                    IdentityClaim.status.in_(["CONFLICT", "REJECTED"])).limit(1)))
    return {
        "id": v.id, "version_number": v.version_number, "is_current": v.is_current, "state": v.state,
        "processing_status": v.processing_status, "check_status": v.check_status, "review_status": v.review_status,
        "decision_source": "automatic" if db.scalar(select(AuditEvent.id).where(AuditEvent.target_id == v.id,
            AuditEvent.action == "application.automatically_rejected_duplicate").limit(1)) else "staff",
        "pan_status": v.pan_status, "processing_attempt": v.processing_attempt, "row_version": v.row_version,
        "submitted_at": v.submitted_at, "processed_at": v.processed_at, "decided_at": v.decided_at,
        "applicant_message": v.applicant_message, "requested_items": v.requested_items,
        "status": applicant_status(v, has_identity_conflict=conflict),
        "documents": docs, "checks": [serialize_check(c, staff=staff) for c in checks],
    }


def serialize_policy(p: OrgPolicy) -> dict:
    return {"pan_policy": p.pan_policy, "max_salary_slips": p.max_salary_slips,
            "required_salary_slips": p.required_salary_slips, "accept_bank_statement": p.accept_bank_statement,
            "max_file_mb": p.max_file_mb, "retention_days_after_decision": p.retention_days_after_decision}


def history(app: Application) -> list[dict]:
    return [{"id": v.id, "version_number": v.version_number, "state": v.state, "is_current": v.is_current,
             "processing_status": v.processing_status, "check_status": v.check_status,
             "review_status": v.review_status, "submitted_at": v.submitted_at, "decided_at": v.decided_at}
            for v in sorted(app.versions, key=lambda x: -x.version_number)]


def serialize_event(e: ProcessingEvent) -> dict:
    try:
        label = STAGE_LABELS[Stage(e.stage)]
    except ValueError:
        label = e.stage
    return {"id": e.id, "version_id": e.version_id, "document_id": e.document_id, "attempt": e.attempt,
            "stage": e.stage, "stage_label": label, "status": e.status, "message": e.message,
            "created_at": e.created_at}


def current_version(app: Application) -> ApplicationVersion:
    return next(v for v in app.versions if v.is_current)
