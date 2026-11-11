"""Reject later cross-account reuse without changing the earlier account's decision."""
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (Application, ApplicationVersion, ClaimStatus, ConfirmedIdentity,
                        Document, IdentityClaim, Role, StaffMembership, User, VerificationCheck)
from app.services.audit import audit
from app.services.events import emit
from app.services.notify import notify
from app.services.retention import schedule_purge_for_application

MESSAGE = ("These identity documents or identifiers have already been used for another approved account. "
           "This submission has been automatically rejected. If the existing account is yours, please sign in to it. "
           "If you believe this is an error or your documents were used without permission, contact support for review. "
           "Only submit your own documents or documents you are authorized to provide.")
ACTION = "application.automatically_rejected_duplicate"


def earlier_reuse(db, version):
    app = db.get(Application, version.application_id)
    conflicts = []
    mine = db.scalars(select(IdentityClaim).where(IdentityClaim.version_id == version.id)).all()
    for claim in mine:
        approved = db.scalars(select(IdentityClaim).join(ApplicationVersion, IdentityClaim.version_id == ApplicationVersion.id)
            .join(Application, ApplicationVersion.application_id == Application.id)
            .join(User, Application.applicant_id == User.id)
            .where(IdentityClaim.fingerprint == claim.fingerprint,
                   Application.applicant_id != app.applicant_id, ApplicationVersion.is_current.is_(True),
                   ApplicationVersion.review_status == "APPROVED", User.is_active.is_(True))).all()
        if approved:
            conflicts.append((claim.document_id, {c.application_id for c in approved}))
    for doc in version.documents:
        rows = db.execute(select(Document, Application).join(ApplicationVersion, Document.version_id == ApplicationVersion.id)
            .join(Application, ApplicationVersion.application_id == Application.id)
            .join(User, Application.applicant_id == User.id)
            .where(Document.content_fingerprint == doc.content_fingerprint,
                   Application.applicant_id != app.applicant_id, ApplicationVersion.is_current.is_(True),
                   ApplicationVersion.review_status == "APPROVED", User.is_active.is_(True))).all()
        if rows:
            conflicts.append((doc.id, {other_app.id for _, other_app in rows}))
    return conflicts


def reject_duplicate(db, version, conflicts):
    app = db.get(Application, version.application_id)
    version.review_status = "REJECTED"
    version.decided_at = datetime.now(UTC)
    version.applicant_message = MESSAGE
    version.requested_items = None
    affected = {app.id}
    checked_documents = set()
    for document_id, previous_apps in conflicts:
        affected.update(previous_apps)
        if document_id in checked_documents:
            continue
        checked_documents.add(document_id)
        db.add(VerificationCheck(version_id=version.id, attempt=version.processing_attempt,
            check_type="DUPLICATE_DOCUMENT_POLICY", document_id=document_id,
            status="MISMATCH", code="DOCUMENTS_ALREADY_USED", detail={}))
    claims = db.scalars(select(IdentityClaim).where(IdentityClaim.version_id == version.id)).all()
    for claim in claims:
        claim.status = ClaimStatus.REJECTED
        others = db.scalars(select(IdentityClaim).where(IdentityClaim.fingerprint == claim.fingerprint,
                           IdentityClaim.user_id != app.applicant_id)).all()
        for other in others:
            if other.status == ClaimStatus.CONFLICT:
                # The later reuse is rejected; don't leave the earlier account blocked.
                confirmed = db.scalar(select(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == other.fingerprint))
                other.status = ClaimStatus.CONFIRMED if confirmed and confirmed.user_id == other.user_id else ClaimStatus.UNVERIFIED
    audit(db, action=ACTION, target_type="application_version", target_id=version.id,
          application_id=app.id, organization_id=app.organization_id, decision="REJECTED",
          reason_code="DOCUMENTS_ALREADY_USED", attempt=version.processing_attempt)
    emit(db, version, "DECISION", "info", "Automatically rejected: documents or identifiers already used by another account.", commit=False)
    notify(db, app.applicant_id, "REVIEW_REJECTED", "Application automatically rejected - documents already used", MESSAGE, app.id)
    for aid in affected:
        target = db.get(Application, aid)
        admin_ids = db.scalars(select(StaffMembership.user_id).join(User).where(
            StaffMembership.organization_id == target.organization_id, StaffMembership.role == Role.ADMIN,
            User.role == Role.ADMIN, User.is_active.is_(True))).all()
        for admin_id in set(admin_ids):
            notify(db, admin_id, "DUPLICATE_AUTO_REJECTED", "Duplicate documents - automatic rejection",
                   f"Cross-account document reuse was detected for application {target.reference}; the later submission was automatically rejected. "
                   "Review this application’s duplicate evidence. Reuse is not by itself proof of forgery.", target.id)
        if aid != app.id:
            notify(db, target.applicant_id, "IDENTITY_REUSE_BLOCKED", "Reuse of your documents was blocked",
                   "A later submission reused documents or identifiers associated with your account and was rejected. "
                   "Your existing application decision has not changed. Contact support if this activity was unexpected.", target.id)
    schedule_purge_for_application(db, app, version.decided_at)
