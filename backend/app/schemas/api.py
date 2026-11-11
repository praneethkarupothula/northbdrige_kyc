"""Request/response schemas exposed in OpenAPI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Msg(BaseModel):
    detail: str


# ----------------------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    password_confirm: str = Field(min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")  # a "role" field is rejected outright

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not any(ch.isalpha() for ch in v):
            raise ValueError("Enter your full name")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class EmailIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=128)
    password_confirm: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    password: str
    password_confirm: str


class ProfileIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    email_verified: bool
    portal: str
    organization_ids: list[str] = []


# ----------------------------------------------------------------------- applicant
class DraftUpdateIn(BaseModel):
    pan_choice: Literal["SUPPLIED", "UNAVAILABLE"] | None = None


class SubmitIn(BaseModel):
    consent: bool
    row_version: int


class RowVersionIn(BaseModel):
    row_version: int


class StatusOut(BaseModel):
    key: str
    tone: str
    headline: str
    detail: str


class DocumentOut(BaseModel):
    id: str
    category: str
    file_format: str
    size_bytes: int
    page_count: int
    pages_processed: list[int] | None
    processing_status: str
    current_stage: str | None
    error_code: str | None
    error_message: str | None
    quality_flags: list[str] = []
    quality_suggestions: list[str] = []
    uploaded_at: datetime
    purged: bool
    duplicate_of: str | None = None
    extraction: dict[str, Any] | None = None
    attention: str | None = None


class CheckOut(BaseModel):
    id: str
    check_type: str
    status: str
    code: str
    document_id: str | None
    reference_document_id: str | None
    detail: dict
    explanation: str


class EventOut(BaseModel):
    id: int
    version_id: str
    document_id: str | None
    attempt: int
    stage: str
    stage_label: str
    status: str
    message: str
    created_at: datetime


class VersionOut(BaseModel):
    id: str
    version_number: int
    is_current: bool
    state: str
    processing_status: str
    check_status: str | None
    review_status: str
    decision_source: str = "staff"
    pan_status: str
    processing_attempt: int
    row_version: int
    submitted_at: datetime | None
    processed_at: datetime | None
    decided_at: datetime | None
    applicant_message: str | None
    requested_items: list | None
    status: StatusOut
    documents: list[DocumentOut]
    checks: list[CheckOut]


class PolicyOut(BaseModel):
    pan_policy: str
    max_salary_slips: int
    required_salary_slips: int
    accept_bank_statement: bool
    max_file_mb: int
    retention_days_after_decision: int


class ApplicationOut(BaseModel):
    id: str
    reference: str
    created_at: datetime
    current: VersionOut
    history: list[dict]
    policy: PolicyOut


# --------------------------------------------------------------------------- staff
class ApproveIn(BaseModel):
    version_id: str
    row_version: int
    internal_note: str | None = Field(None, max_length=4000)


class RejectIn(BaseModel):
    version_id: str
    row_version: int
    reason_code: Literal["DOCUMENTS_INCONSISTENT", "IDENTITY_NOT_ESTABLISHED", "SUSPECTED_ALTERED_DOCUMENT",
                         "DUPLICATE_ACCOUNT", "OTHER"]
    applicant_message: str = Field(min_length=10, max_length=2000)
    internal_note: str | None = Field(None, max_length=4000)


class RequestedItem(BaseModel):
    category: Literal["AADHAAR", "PAN", "SALARY_SLIP", "BANK_STATEMENT", "OTHER"]
    instruction: str = Field(min_length=5, max_length=500)


class ChangesIn(BaseModel):
    version_id: str
    row_version: int
    items: list[RequestedItem] = Field(min_length=1, max_length=10)
    applicant_message: str = Field(min_length=10, max_length=2000)
    internal_note: str | None = Field(None, max_length=4000)


class NoteIn(BaseModel):
    version_id: str
    note: str = Field(min_length=1, max_length=4000)


class StaffCreateIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    role: Literal["reviewer", "admin"]


class StaffUpdateIn(BaseModel):
    role: Literal["reviewer", "admin"] | None = None
    is_active: bool | None = None


class PolicyIn(BaseModel):
    pan_policy: Literal["required"]
    max_salary_slips: int = Field(ge=1, le=12)
    required_salary_slips: int = Field(ge=1, le=12)
    accept_bank_statement: bool
    max_file_mb: int = Field(ge=1, le=10)
    retention_days_after_decision: int = Field(ge=0, le=3650)


class ResolveConflictIn(BaseModel):
    fingerprint_ref: str  # id of any claim in the group (fingerprints are never sent to the browser)
    holder_claim_id: str | None = None
    note: str = Field(min_length=5, max_length=2000)


class LifecycleIn(BaseModel):
    version_id: str
    row_version: int
    applicant_message: str = Field(min_length=10, max_length=2000)
