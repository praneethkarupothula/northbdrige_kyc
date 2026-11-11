from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    APPLICANT = "applicant"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class VersionState(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    SUPERSEDED = "SUPERSEDED"


class ProcessingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_REUPLOAD = "NEEDS_REUPLOAD"


class CheckStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewStatus(StrEnum):
    NOT_READY = "NOT_READY"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class DocumentCategory(StrEnum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    SALARY_SLIP = "SALARY_SLIP"
    BANK_STATEMENT = "BANK_STATEMENT"


class PanStatus(StrEnum):
    PENDING = "PENDING"  # draft, applicant has not decided yet
    SUPPLIED = "SUPPLIED"
    UNAVAILABLE = "UNAVAILABLE"  # applicant ticked "I do not have a PAN card"
    NOT_REQUESTED = "NOT_REQUESTED"  # organisation policy does not request PAN


class IdType(StrEnum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"


class ClaimStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"  # claimed by an applicant, not yet confirmed by staff
    CONFLICT = "CONFLICT"  # another account claims/holds the same identifier
    CONFIRMED = "CONFIRMED"  # promoted on approval / staff resolution
    REJECTED = "REJECTED"  # staff decided this account is not the holder
    UNTRUSTED = "UNTRUSTED"  # OCR uncertain - never reserves the identifier


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    NOTE = "NOTE"


class Stage(StrEnum):
    UPLOAD_RECEIVED = "UPLOAD_RECEIVED"
    FILE_VALIDATION = "FILE_VALIDATION"
    QUALITY_INSPECTION = "QUALITY_INSPECTION"
    TEXT_EXTRACTION = "TEXT_EXTRACTION"
    OCR_SCHEMA_VALIDATION = "OCR_SCHEMA_VALIDATION"
    PII_MASKING = "PII_MASKING"
    LLM_EXTRACTION = "LLM_EXTRACTION"
    LLM_TELEMETRY = "LLM_TELEMETRY"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    NAME_CHECKS = "NAME_CHECKS"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    DECISION = "DECISION"


STAGE_ORDER = list(Stage)

STAGE_LABELS = {
    Stage.UPLOAD_RECEIVED: "Upload received",
    Stage.FILE_VALIDATION: "File validation",
    Stage.QUALITY_INSPECTION: "Quality inspection",
    Stage.TEXT_EXTRACTION: "PDF text extraction / image OCR",
    Stage.OCR_SCHEMA_VALIDATION: "Pydantic OCR schema validation",
    Stage.PII_MASKING: "PII detection and masking",
    Stage.LLM_EXTRACTION: "Gemini structured extraction",
    Stage.LLM_TELEMETRY: "Gemini call telemetry",
    Stage.SCHEMA_VALIDATION: "Schema validation",
    Stage.DUPLICATE_CHECK: "Identity duplicate check",
    Stage.NAME_CHECKS: "Name consistency checks",
    Stage.AWAITING_REVIEW: "Awaiting human review",
    Stage.DECISION: "Reviewer decision",
}
