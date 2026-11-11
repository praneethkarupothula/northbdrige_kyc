"""Duplicate-identity detection.

* Identifiers are canonicalised locally and fingerprinted with HMAC-SHA-256(server secret,
  "<TYPE>:<value>"). Plaintext identifiers are never stored for lookup.
* An applicant's upload creates an UNVERIFIED claim. Only staff action (approval or audited
  conflict resolution) creates a ConfirmedIdentity, which is unique per fingerprint.
* An unverified first upload therefore cannot permanently lock out the legitimate holder:
  only identifiers on a current approved application trigger a cross-account conflict.
* OCR-uncertain or format-implausible identifiers become UNTRUSTED claims that reserve nothing.
* Duplicate detection never relies on names.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_str, identity_fingerprint
from app.core.db import is_postgres
from app.models import (Application, ApplicationVersion, ClaimStatus, ConfirmedIdentity, IdentityClaim, IdType,
                        Notification, Role, StaffMembership, User)
from app.services.notify import notify

UNCERTAIN_OCR_CONF = 80.0
GENERIC_DUPLICATE_MESSAGE = "This identity is already associated with an account. Please sign in or contact support."
DOCUMENT_CAUTION = ("Only submit documents that belong to you or that you are authorized to provide. "
                    "Do not use another person's identity or submit forged or altered documents. "
                    "A conflict requires review and does not by itself prove misuse.")


def approved_claims(db, fingerprint):
    return db.scalars(select(IdentityClaim).join(ApplicationVersion, IdentityClaim.version_id == ApplicationVersion.id)
        .join(Application, ApplicationVersion.application_id == Application.id)
        .join(User, Application.applicant_id == User.id)
        .where(IdentityClaim.fingerprint == fingerprint, ApplicationVersion.is_current.is_(True),
               ApplicationVersion.review_status == "APPROVED", User.is_active.is_(True))).all()


def _notify_conflict(db: Session, mine: IdentityClaim) -> None:
    claims = [mine] + [c for c in approved_claims(db, mine.fingerprint) if c.user_id != mine.user_id]
    for claim in claims:
        if claim.status == ClaimStatus.UNTRUSTED:
            continue
        app = db.get(Application, claim.application_id)
        version = db.get(ApplicationVersion, claim.version_id)
        recipients = [(claim.user_id, "Identity document conflict", GENERIC_DUPLICATE_MESSAGE + " " + DOCUMENT_CAUTION)]
        admins = db.scalars(select(User).join(StaffMembership).where(
            StaffMembership.organization_id == app.organization_id, User.role == Role.ADMIN, User.is_active.is_(True))).all()
        recipients += [(admin.id, "Identity conflict requires review", f"Application {app.reference}: a submitted identifier is also associated with another account. "
                        "Open this application and review the Identity tab before making a decision. This is not proof of forgery.") for admin in admins]
        for user_id, title, body in recipients:
            existing = select(Notification.id).where(Notification.user_id == user_id,
                Notification.application_id == app.id, Notification.kind == "IDENTITY_CONFLICT")
            if version.submitted_at:
                existing = existing.where(Notification.created_at >= version.submitted_at)
            if db.scalar(existing.limit(1)) is None:
                notify(db, user_id, "IDENTITY_CONFLICT", title, body, app.id)
                db.flush()



@dataclass
class IdentityInput:
    id_type: str
    canonical: str | None
    plausible: bool
    ocr_conf: float | None  # None for embedded PDF text (no OCR confidence exists)
    masked: str
    name: str | None
    document_id: str


@dataclass
class IdentityOutcome:
    id_type: str
    status: str  # CheckStatus value
    code: str
    claim_status: str | None
    masked: str


def _lock(db: Session, fingerprint: str) -> None:
    """Serialise concurrent claims for the same identifier (transaction-scoped)."""
    if is_postgres(db):
        key = int(fingerprint[:15], 16)
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def register_claim(db: Session, *, user_id: str, application_id: str, version_id: str, item: IdentityInput) -> IdentityOutcome:
    from app.models.enums import CheckStatus

    if not item.canonical:
        return IdentityOutcome(item.id_type, CheckStatus.INCOMPLETE, "IDENTIFIER_NOT_EXTRACTED", None, item.masked)
    fp = identity_fingerprint(item.id_type, item.canonical)
    uncertain = (not item.plausible) or (item.ocr_conf is not None and item.ocr_conf < UNCERTAIN_OCR_CONF)
    _lock(db, fp)

    mine = db.scalar(select(IdentityClaim).where(IdentityClaim.fingerprint == fp, IdentityClaim.user_id == user_id))
    name_enc = encrypt_str(item.name, "identity-name") if item.name else None
    if mine is None:
        mine = IdentityClaim(id_type=item.id_type, fingerprint=fp, user_id=user_id, application_id=application_id,
                             version_id=version_id, document_id=item.document_id, status=ClaimStatus.UNVERIFIED,
                             name_enc=name_enc, masked_value=item.masked)
        db.add(mine)
    else:  # same applicant resubmitting their own identity -> update existing workflow
        mine.version_id, mine.document_id, mine.application_id = version_id, item.document_id, application_id
        mine.name_enc, mine.masked_value = name_enc, item.masked

    if uncertain:
        mine.status = ClaimStatus.UNTRUSTED
        db.flush()
        code = "IDENTIFIER_FORMAT_IMPLAUSIBLE" if not item.plausible else "IDENTIFIER_OCR_UNCERTAIN"
        return IdentityOutcome(item.id_type, CheckStatus.INCOMPLETE, code, mine.status, item.masked)

    approved = approved_claims(db, fp)
    others = [c for c in approved if c.user_id != user_id]
    if others:
        mine.status = ClaimStatus.CONFLICT
        db.flush()
        _notify_conflict(db, mine)
        return IdentityOutcome(item.id_type, CheckStatus.MISMATCH, "IDENTITY_HELD_BY_ANOTHER_ACCOUNT",
                               mine.status, item.masked)
    mine.status = ClaimStatus.CONFIRMED if any(c.user_id == user_id for c in approved) else ClaimStatus.UNVERIFIED
    db.flush()
    return IdentityOutcome(item.id_type, CheckStatus.MATCH,
                           "IDENTITY_CONFIRMED_FOR_THIS_ACCOUNT" if mine.status == ClaimStatus.CONFIRMED else "NO_CONFLICTING_CLAIM",
                           mine.status, item.masked)


class IdentityPromotionConflict(Exception):
    pass


def promote_claims_on_approval(db: Session, version_id: str, reviewer_id: str) -> None:
    """Approval confirms this version's UNVERIFIED/CONFIRMED claims. Unique fingerprint makes it atomic."""
    claims = db.scalars(select(IdentityClaim).where(IdentityClaim.version_id == version_id)).all()
    for c in claims:
        if c.status == ClaimStatus.UNTRUSTED:
            continue  # Consistency approval must never validate or reserve sample/uncertain identifiers.
        if c.status in (ClaimStatus.CONFLICT, ClaimStatus.REJECTED):
            raise IdentityPromotionConflict(c.status)
        _lock(db, c.fingerprint)
        existing = db.scalar(select(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == c.fingerprint))
        if existing is not None and not any(a.user_id == existing.user_id for a in approved_claims(db, c.fingerprint)):
            db.delete(existing)
            db.flush()
            existing = None
        if existing is not None:
            if existing.user_id != c.user_id:
                raise IdentityPromotionConflict("HELD_BY_OTHER")
            c.status = ClaimStatus.CONFIRMED
            continue
        try:
            with db.begin_nested():
                db.add(ConfirmedIdentity(fingerprint=c.fingerprint, id_type=c.id_type, user_id=c.user_id,
                                         name_enc=c.name_enc, confirmed_by=reviewer_id, source="approval"))
        except IntegrityError:
            raise IdentityPromotionConflict("RACE") from None
        c.status = ClaimStatus.CONFIRMED


def resolve_conflict(db: Session, *, fingerprint: str, holder_claim_id: str | None, admin_id: str) -> list[IdentityClaim]:
    """Admin decision: confirm one claim as the holder (others REJECTED), or reject all claims.

    Returns the affected claims so the caller can re-queue processing for their versions.
    """
    _lock(db, fingerprint)
    claims = db.scalars(select(IdentityClaim).where(IdentityClaim.fingerprint == fingerprint)).all()
    if holder_claim_id and holder_claim_id not in {c.id for c in claims}:
        raise ValueError("claim does not belong to this identity")
    if holder_claim_id:
        holder = next(c for c in claims if c.id == holder_claim_id)
        db.execute(update(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == fingerprint)
                   .values(user_id=holder.user_id, confirmed_by=admin_id, source="conflict_resolution"))
        if db.scalar(select(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == fingerprint)) is None:
            db.add(ConfirmedIdentity(fingerprint=fingerprint, id_type=holder.id_type, user_id=holder.user_id,
                                     name_enc=holder.name_enc, confirmed_by=admin_id, source="conflict_resolution"))
    for c in claims:
        c.status = ClaimStatus.CONFIRMED if c.id == holder_claim_id else ClaimStatus.REJECTED
        c.resolved_by = admin_id
    db.flush()
    return list(claims)


def id_type_for_category(category: str) -> str | None:
    return {"AADHAAR": IdType.AADHAAR, "PAN": IdType.PAN}.get(category)
