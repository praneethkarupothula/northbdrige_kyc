"""SQLAlchemy 2 ORM models.

Sensitive values (exact extracted names, identifiers, DOB, original filenames) are stored
only as AES-GCM ciphertext (``*_enc`` columns). Identity lookups use keyed HMAC fingerprints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


ID = String(36)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC and restore the offset SQLite drops on timestamp reads."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


TS = UTCDateTime()


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class OrgPolicy(Base):
    """Administrator-configurable prototype policy (not a legal determination)."""

    __tablename__ = "org_policies"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    pan_policy: Mapped[str] = mapped_column(String(20), default="required")
    max_salary_slips: Mapped[int] = mapped_column(Integer, default=6)
    required_salary_slips: Mapped[int] = mapped_column(Integer, default=1)
    accept_bank_statement: Mapped[bool] = mapped_column(Boolean, default=True)
    max_file_mb: Mapped[int] = mapped_column(Integer, default=10)
    retention_days_after_decision: Mapped[int] = mapped_column(Integer, default=7)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    memberships: Mapped[list[StaffMembership]] = relationship(back_populates="user", lazy="selectin")


class StaffMembership(Base):
    __tablename__ = "staff_memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    user: Mapped[User] = relationship(back_populates="memberships")


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal: Mapped[str] = mapped_column(String(20))  # applicant | staff
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AuthToken(Base):
    """Single-use expiring tokens for email verification and password reset."""

    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class EmailOutbox(Base):
    """Development email capture. Clearly labelled; only readable in development/test."""

    __tablename__ = "email_outbox"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    to_address: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    transport: Mapped[str] = mapped_column(String(20))  # dev-capture | smtp
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("applicant_id", "organization_id"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    versions: Mapped[list[ApplicationVersion]] = relationship(
        back_populates="application", order_by="ApplicationVersion.version_number"
    )


class ApplicationVersion(Base):
    __tablename__ = "application_versions"
    __table_args__ = (
        UniqueConstraint("application_id", "version_number"),
        Index(
            "uq_one_current_version",
            "application_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_versions_queue", "review_status", "submitted_at"),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    processing_status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED", index=True)
    check_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    review_status: Mapped[str] = mapped_column(String(24), default="NOT_READY")
    processing_attempt: Mapped[int] = mapped_column(Integer, default=0)
    pan_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    consent_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    applicant_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # reviewer-written, applicant-visible
    requested_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency token
    parent_version_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    application: Mapped[Application] = relationship(back_populates="versions")
    documents: Mapped[list[Document]] = relationship(
        back_populates="version", order_by="Document.uploaded_at", lazy="selectin"
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("application_versions.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)  # NULL once purged
    file_format: Mapped[str] = mapped_column(String(10))  # pdf | jpeg | png
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # keyed HMAC of bytes
    original_filename_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    pages_processed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED")
    current_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    purge_after: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    version: Mapped[ApplicationVersion] = relationship(back_populates="documents")


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    __table_args__ = (UniqueConstraint("document_id", "attempt"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    document_type: Mapped[str] = mapped_column(String(20))
    fields_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # exact values (encrypted JSON)
    display_fields: Mapped[dict] = mapped_column(JSON)  # masked / non-identifying values for default view
    provenance: Mapped[dict] = mapped_column(JSON)  # field -> {page, bbox, method}
    pay_period_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pay_period_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_salary: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_salary: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    monthly_salary: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    extraction_method: Mapped[str] = mapped_column(String(30))
    ocr_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class VerificationCheck(Base):
    __tablename__ = "verification_checks"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("application_versions.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    check_type: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    reference_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # never contains raw PII
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class IdentityClaim(Base):
    __tablename__ = "identity_claims"
    __table_args__ = (UniqueConstraint("fingerprint", "user_id"), Index("ix_claims_fp", "fingerprint"))
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    id_type: Mapped[str] = mapped_column(String(10))
    fingerprint: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("application_versions.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    name_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    masked_value: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ConfirmedIdentity(Base):
    """A staff-confirmed association of an identifier fingerprint with exactly one account."""

    __tablename__ = "confirmed_identities"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    id_type: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(30))  # approval | conflict_resolution
    confirmed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)  # opaque references only
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(TS, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class ProcessingEvent(Base):
    __tablename__ = "processing_events"
    __table_args__ = (Index("ix_events_app_id", "application_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("application_versions.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))  # started | succeeded | failed | info
    message: Mapped[str] = mapped_column(String(300))  # sanitised, never contains PII
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        Index(
            "uq_one_final_decision",
            "version_id",
            unique=True,
            postgresql_where=text("decision IN ('APPROVED','REJECTED')"),
            sqlite_where=text("decision IN ('APPROVED','REJECTED')"),
        ),
    )
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("application_versions.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(24))
    applicant_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # staff-only
    requested_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    check_status_at_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "read_at"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuditEvent(Base):
    """Who did what to which object. Never copies sensitive values into the record."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_target", "target_type", "target_id"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(64))
    application_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
