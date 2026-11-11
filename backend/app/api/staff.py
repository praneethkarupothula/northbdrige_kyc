from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import Principal, authorize_application, require_admin, require_staff
from app.core.crypto import decrypt_str
from app.core.db import get_db, is_postgres
from app.models import (
    Application, ApplicationVersion, AuditEvent, CheckStatus, ClaimStatus, ConfirmedIdentity, Decision, Document,
    IdentityClaim, JobStatus, ProcessingJob, ProcessingStatus, Review, ReviewStatus, Role, Stage, StaffMembership,
    User, VersionState,
)
from app.pipeline.identifiers import mask_name, normalize_name
from app.schemas.api import LifecycleIn, ApproveIn, ChangesIn, Msg, NoteIn, PolicyIn, RejectIn, ResolveConflictIn, StaffCreateIn, StaffUpdateIn
from app.services import identity as identity_svc
from app.services.audit import audit
from app.services.events import emit
from app.services.notify import notify
from app.services.policy import get_policy
from app.services.retention import schedule_purge_for_application
from app.services.views import current_version, history, serialize_event, serialize_policy, serialize_version, version_checks, explain
from app.api.applications import enqueue_processing

router = APIRouter(prefix="/v1/staff", tags=["staff"])
admin = APIRouter(prefix="/v1/admin", tags=["admin"])


def _mask_email(e: str) -> str:
    local, _, dom = e.partition("@")
    return (local[:1] + "***@" + dom) if dom else "***"


def _age_hours(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - ts).total_seconds() / 3600, 1)


def _org_filter(p: Principal):
    return Application.organization_id.in_(p.org_ids() or {"__none__"})


# ============================================================================ dashboard
@router.get("/dashboard")
def dashboard(p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    base = (select(func.count()).select_from(ApplicationVersion).join(Application)
            .where(_org_filter(p), ApplicationVersion.is_current.is_(True)))
    count = lambda *c: db.scalar(base.where(*c))  # noqa: E731
    oldest = db.scalar(select(func.min(ApplicationVersion.processed_at)).join(Application).where(
        _org_filter(p), ApplicationVersion.is_current.is_(True),
        ApplicationVersion.review_status == ReviewStatus.PENDING_REVIEW))
    workload = db.execute(select(User.full_name, User.id, func.count()).join(
        ApplicationVersion, ApplicationVersion.assigned_reviewer_id == User.id).join(Application).where(
        _org_filter(p), ApplicationVersion.is_current.is_(True),
        ApplicationVersion.review_status == ReviewStatus.PENDING_REVIEW).group_by(User.id, User.full_name)).all()
    return {
        "pending_review": count(ApplicationVersion.review_status == ReviewStatus.PENDING_REVIEW),
        "mismatches": count(ApplicationVersion.check_status == CheckStatus.MISMATCH),
        "incomplete": count(ApplicationVersion.check_status == CheckStatus.INCOMPLETE),
        "approved": count(ApplicationVersion.review_status == ReviewStatus.APPROVED),
        "rejected": count(ApplicationVersion.review_status == ReviewStatus.REJECTED),
        "changes_requested": count(ApplicationVersion.review_status == ReviewStatus.CHANGES_REQUESTED),
        "processing_failures": count(ApplicationVersion.processing_status == ProcessingStatus.FAILED),
        "processing": count(ApplicationVersion.processing_status.in_([ProcessingStatus.QUEUED, ProcessingStatus.RUNNING])),
        "unassigned_pending": count(ApplicationVersion.review_status == ReviewStatus.PENDING_REVIEW,
                                    ApplicationVersion.assigned_reviewer_id.is_(None)),
        "oldest_pending_age_hours": _age_hours(oldest),
        "workload": [{"reviewer": name, "reviewer_id": rid, "assigned_pending": n} for name, rid, n in workload],
    }


# ============================================================================ queue
@router.get("/applications")
def queue_list(
    p: Principal = Depends(require_staff), db: Session = Depends(get_db),
    review_status: str | None = None, check_status: str | None = None, processing_status: str | None = None,
    document_type: str | None = None, assignment: str | None = Query(None, pattern="^(me|unassigned|any)$"),
    submitted_from: date | None = None, submitted_to: date | None = None, q: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    stmt = (select(ApplicationVersion, Application, User).join(Application, ApplicationVersion.application_id == Application.id)
            .join(User, User.id == Application.applicant_id)
            .where(_org_filter(p), ApplicationVersion.is_current.is_(True)))
    if review_status:
        stmt = stmt.where(ApplicationVersion.review_status == review_status)
    if check_status:
        stmt = stmt.where(ApplicationVersion.check_status == check_status)
    if processing_status == "IN_PROGRESS":
        stmt = stmt.where(ApplicationVersion.processing_status.in_([ProcessingStatus.QUEUED, ProcessingStatus.RUNNING]))
    elif processing_status:
        stmt = stmt.where(ApplicationVersion.processing_status == processing_status)
    if document_type:
        stmt = stmt.where(ApplicationVersion.id.in_(select(Document.version_id).where(Document.category == document_type)))
    if assignment == "me":
        stmt = stmt.where(ApplicationVersion.assigned_reviewer_id == p.user.id)
    elif assignment == "unassigned":
        stmt = stmt.where(ApplicationVersion.assigned_reviewer_id.is_(None))
    if submitted_from:
        stmt = stmt.where(ApplicationVersion.submitted_at >= datetime.combine(submitted_from, datetime.min.time(), UTC))
    if submitted_to:
        stmt = stmt.where(ApplicationVersion.submitted_at < datetime.combine(submitted_to + timedelta(days=1),
                                                                             datetime.min.time(), UTC))
    if q:
        stmt = stmt.where(or_(Application.reference.ilike(f"%{q.strip()}%"), User.email == q.strip().lower()))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(ApplicationVersion.submitted_at.asc().nulls_last())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    reviewers = {u.id: u.full_name for u in db.scalars(select(User).where(User.role.in_([Role.REVIEWER, Role.ADMIN])))}
    items = [{
        "application_id": a.id, "reference": a.reference, "version_id": v.id, "version_number": v.version_number,
        "applicant": u.full_name if p.role == Role.ADMIN else mask_name(u.full_name),
        "state": v.state, "processing_status": v.processing_status,
        "check_status": v.check_status, "review_status": v.review_status, "submitted_at": v.submitted_at,
        "age_hours": _age_hours(v.submitted_at), "assigned_reviewer": reviewers.get(v.assigned_reviewer_id),
        "assigned_to_me": v.assigned_reviewer_id == p.user.id,
        "document_types": sorted({d.category for d in v.documents}),
    } for v, a, u in rows]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _app_for_staff(db: Session, p: Principal, app_id: str) -> Application:
    return authorize_application(p, db.get(Application, app_id))


def _identity_view(db: Session, v: ApplicationVersion, p: Principal) -> list[dict]:
    out = []
    for c in db.scalars(select(IdentityClaim).where(IdentityClaim.version_id == v.id)):
        name = decrypt_str(c.name_enc, "identity-name")
        others = db.scalars(select(IdentityClaim).where(IdentityClaim.fingerprint == c.fingerprint,
                                                        IdentityClaim.id != c.id)).all()
        confirmed = db.scalar(select(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == c.fingerprint))
        record = None
        if confirmed is not None:
            cname = decrypt_str(confirmed.name_enc, "identity-name")
            record = {"held_by_this_account": confirmed.user_id == c.user_id, "source": confirmed.source,
                      "name_agrees": (normalize_name(cname) == normalize_name(name)) if cname and name else None}
        duplicate_accounts = []
        for other in others:
            other_app = db.get(Application, other.application_id)
            if other_app.organization_id not in p.org_ids():
                continue
            other_user = db.get(User, other.user_id)
            duplicate_accounts.append({
                "application_id": other_app.id, "reference": other_app.reference, "claim_status": other.status,
                "review_status": db.get(ApplicationVersion, other.version_id).review_status,
                "account_name": other_user.full_name if p.role == Role.ADMIN else mask_name(other_user.full_name),
                "document_name": decrypt_str(other.name_enc, "identity-name") if p.role == Role.ADMIN and other.name_enc else None,
            })
        out.append({
            "duplicate_accounts": duplicate_accounts,
            "claim_id": c.id, "id_type": c.id_type, "masked_value": c.masked_value, "status": c.status,
            "other_account_claims": len(others),
            "other_claim_name_agreement": [
                (normalize_name(decrypt_str(o.name_enc, "identity-name") or "") == normalize_name(name or ""))
                if o.name_enc and name else None for o in others],
            "confirmed_record": record,
        })
    return out


def _duplicate_documents_view(db, version, p):
    app = db.get(Application, version.application_id)
    duplicates = []
    for doc in version.documents:
        rows = db.execute(select(Document, Application).join(ApplicationVersion, Document.version_id == ApplicationVersion.id)
            .join(Application, ApplicationVersion.application_id == Application.id)
            .where(Document.content_fingerprint == doc.content_fingerprint, Application.applicant_id != app.applicant_id,
                   Application.organization_id.in_(p.org_ids()))).all()
        for other, other_app in rows:
            user = db.get(User, other_app.applicant_id)
            duplicates.append({"document_id": doc.id, "category": doc.category, "application_id": other_app.id,
                "reference": other_app.reference, "account_name": user.full_name if p.role == Role.ADMIN else mask_name(user.full_name)})
    return duplicates


@router.get("/applications/{app_id}")
def detail(app_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    user = db.get(User, app.applicant_id)
    v = current_version(app)
    vids = [x.id for x in app.versions]
    reviews = db.scalars(select(Review).where(Review.version_id.in_(vids)).order_by(Review.created_at)).all()
    reviewer_names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({r.reviewer_id for r in reviews} or {""})))}
    from app.models import ProcessingEvent
    events = db.scalars(select(ProcessingEvent).where(ProcessingEvent.application_id == app.id)
                        .order_by(ProcessingEvent.id.desc()).limit(300)).all()
    audits = db.scalars(select(AuditEvent).where(AuditEvent.application_id == app.id)
                        .order_by(AuditEvent.created_at.desc()).limit(200)).all()
    actor_names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({a.actor_id for a in audits if a.actor_id} or {""})))}
    audit(db, action="application.viewed", target_type="application", target_id=app.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return {
        "id": app.id, "reference": app.reference, "created_at": app.created_at,
        "applicant": {"id": user.id, "name_masked": mask_name(user.full_name), "email_masked": _mask_email(user.email),
                      "full_name": user.full_name if p.role == Role.ADMIN else None,
                      "email": user.email if p.role == Role.ADMIN else None,
                      "is_active": user.is_active, "email_verified": user.email_verified_at is not None, "registered_at": user.created_at},
        "current": serialize_version(db, v, staff=True),
        "assigned_reviewer_id": v.assigned_reviewer_id,
        "assigned_reviewer": db.get(User, v.assigned_reviewer_id).full_name if v.assigned_reviewer_id else None,
        "can_reveal": p.role == Role.ADMIN or v.assigned_reviewer_id == p.user.id,
        "identity": _identity_view(db, v, p),
        "duplicate_documents": _duplicate_documents_view(db, v, p),
        "history": history(app),
        "reviews": [{"id": r.id, "version_id": r.version_id, "decision": r.decision, "reviewer": reviewer_names.get(r.reviewer_id),
                     "applicant_message": r.applicant_message, "internal_note": r.internal_note,
                     "requested_items": r.requested_items, "check_status_at_decision": r.check_status_at_decision,
                     "created_at": r.created_at} for r in reviews],
        "events": [serialize_event(e) for e in reversed(events)],
        "audit": [{"action": a.action, "actor": actor_names.get(a.actor_id, "system"), "actor_role": a.actor_role,
                   "target_type": a.target_type, "details": a.details, "created_at": a.created_at} for a in audits],
        "approval_blockers": approval_blockers(db, v),
    }


@router.get("/applications/{app_id}/versions/{version_id}")
def version_detail(app_id: str, version_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    v = next((x for x in app.versions if x.id == version_id), None)
    if v is None:
        raise HTTPException(404, "Version not found")
    return serialize_version(db, v, staff=True)


# ============================================================================ assignment
@router.post("/applications/{app_id}/claim", response_model=Msg)
def claim_review(app_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    v = current_version(app)
    res = db.execute(update(ApplicationVersion).where(
        ApplicationVersion.id == v.id,
        or_(ApplicationVersion.assigned_reviewer_id.is_(None), ApplicationVersion.assigned_reviewer_id == p.user.id),
    ).values(assigned_reviewer_id=p.user.id, assigned_at=datetime.now(UTC)))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Another reviewer has already claimed this application.")
    audit(db, action="review.claimed", target_type="application_version", target_id=v.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return Msg(detail="Assigned to you")


@router.post("/applications/{app_id}/release", response_model=Msg)
def release_review(app_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    v = current_version(app)
    if v.assigned_reviewer_id not in (p.user.id, None) and p.role != Role.ADMIN:
        raise HTTPException(403, "Only the assigned reviewer or an administrator can release this review.")
    v.assigned_reviewer_id, v.assigned_at = None, None
    audit(db, action="review.released", target_type="application_version", target_id=v.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return Msg(detail="Released")


# ============================================================================ decisions
def approval_blockers(db: Session, v: ApplicationVersion) -> list[str]:
    b = []
    applicant = db.get(User, db.get(Application, v.application_id).applicant_id)
    if not applicant.is_active:
        b.append("This applicant account is closed.")
    if not v.is_current or v.state != VersionState.SUBMITTED:
        b.append("This is not the current submitted version.")
    if v.review_status != ReviewStatus.PENDING_REVIEW:
        b.append(f"Review status is {v.review_status}, not PENDING_REVIEW.")
    if v.processing_status != ProcessingStatus.SUCCEEDED:
        b.append(f"Processing status is {v.processing_status}.")
    from app.services.duplicate_policy import earlier_reuse
    if earlier_reuse(db, v):
        b.append("Documents or identifiers are already used by another approved account. Reprocess this submission.")
    checks = version_checks(db, v)
    ocr_uncertain = any(c.code == "NAME_OCR_UNCERTAIN" for c in checks)
    if v.check_status != CheckStatus.MATCH:
        if v.check_status == CheckStatus.INCOMPLETE and ocr_uncertain:
            b.append("A document name has an OCR character ambiguity (such as I versus l). No name mismatch is confirmed. "
                     "Request a clearer copy of the affected document and reprocess it.")
        else:
            b.append(f"Automated result is {v.check_status or 'not available'}; request a correction instead of approving.")
    pan_check = next((c for c in checks if c.check_type == "NAME_PAN_VS_AADHAAR"), None)
    if pan_check is None:
        b.append("The PAN/Aadhaar name comparison is missing. Reprocess under the current matching rules.")
    elif pan_check.status != CheckStatus.MATCH and pan_check.code != "NAME_OCR_UNCERTAIN":
        b.append("PAN/Aadhaar name comparison: " + explain(pan_check.code))
    duplicates = {c.document_id for c in checks if c.code == "IDENTICAL_FILE_REPEATED"}
    for doc in v.documents:
        if doc.category == "SALARY_SLIP" and doc.id not in duplicates:
            required = {"NAME_SALARY_VS_AADHAAR", "NAME_SALARY_VS_PAN", "PAN_NUMBER_SALARY_VS_PAN"}
            document_checks = {c.check_type: c for c in checks if c.document_id == doc.id}
            if not required <= document_checks.keys():
                b.append("Required salary slip comparison results are missing. Reprocess under the current rules.")
                break
            failures = [document_checks[k] for k in sorted(required)
                        if document_checks[k].status != CheckStatus.MATCH and document_checks[k].code != "NAME_OCR_UNCERTAIN"]
            if failures:
                b.append("Salary slip comparison: " + explain(failures[0].code))
                break
    bad = db.scalar(select(func.count()).select_from(IdentityClaim).where(
        IdentityClaim.version_id == v.id,
        IdentityClaim.status.in_([ClaimStatus.CONFLICT, ClaimStatus.REJECTED])))
    if bad:
        b.append("Unresolved identity conflict with another account.")
    return b


def _locked_current(db: Session, p: Principal, app_id: str, version_id: str, row_version: int):
    app = _app_for_staff(db, p, app_id)
    q = select(ApplicationVersion).where(ApplicationVersion.id == version_id,
                                         ApplicationVersion.application_id == app.id)
    if is_postgres(db):
        q = q.with_for_update()
    v = db.scalar(q)
    if v is None:
        raise HTTPException(404, "Version not found")
    db.refresh(v)
    if not v.is_current:
        raise HTTPException(409, "The applicant has submitted a newer version. Reload before deciding.")
    if v.row_version != row_version:
        raise HTTPException(409, "This application changed since you loaded it. Reload before deciding.")
    if v.assigned_reviewer_id not in (None, p.user.id) and p.role != Role.ADMIN:
        raise HTTPException(409, "This application is assigned to another reviewer.")
    return app, v


def _finish_decision(db: Session, p: Principal, app: Application, v: ApplicationVersion, decision: str,
                     review: Review, notification: tuple[str, str], *, final: bool) -> None:
    v.review_status = decision
    v.decided_at = datetime.now(UTC)
    v.row_version += 1
    if v.assigned_reviewer_id is None:
        v.assigned_reviewer_id = p.user.id
    db.add(review)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A final decision has already been recorded for this version.") from None
    emit(db, v, Stage.DECISION, "info", {"APPROVED": "Approved by Northbridge.", "REJECTED": "Not approved by Northbridge.",
                                         "CHANGES_REQUESTED": "Northbridge requested changes."}[decision], commit=False)
    notify(db, app.applicant_id, f"REVIEW_{decision}", notification[0], notification[1], app.id)
    audit(db, action=f"review.{decision.lower()}", target_type="application_version", target_id=v.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id, decision=decision,
          version_number=v.version_number, from_status="PENDING_REVIEW", to_status=decision,
          reason_code=getattr(review, "_reason_code", None))
    if final:
        schedule_purge_for_application(db, app, v.decided_at)
    db.commit()


@router.post("/applications/{app_id}/approve", response_model=Msg)
def approve(app_id: str, body: ApproveIn, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app, v = _locked_current(db, p, app_id, body.version_id, body.row_version)
    blockers = approval_blockers(db, v)
    if blockers:
        raise HTTPException(409, {"code": "APPROVAL_BLOCKED", "message": " ".join(blockers), "items": blockers})
    try:
        identity_svc.promote_claims_on_approval(db, v.id, p.user.id)
    except identity_svc.IdentityPromotionConflict:
        db.rollback()
        raise HTTPException(409, "An identity on this application conflicts with another account. "
                                 "An administrator must resolve it first.") from None
    v.applicant_message = None
    v.requested_items = None
    _finish_decision(db, p, app, v, ReviewStatus.APPROVED,
                     Review(version_id=v.id, reviewer_id=p.user.id, decision=Decision.APPROVED,
                            internal_note=body.internal_note, check_status_at_decision=v.check_status),
                     ("KYC completed and approved",
                      "Northbridge has reviewed and approved your submitted documents. No further action is needed for this submission. "
                      "Approval confirms document name and PAN-number consistency, not government authentication or loan approval."),
                     final=True)
    return Msg(detail="Approved")


@router.post("/applications/{app_id}/reject", response_model=Msg)
def reject(app_id: str, body: RejectIn, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app, v = _locked_current(db, p, app_id, body.version_id, body.row_version)
    if v.review_status != ReviewStatus.PENDING_REVIEW:
        raise HTTPException(409, f"Cannot reject from status {v.review_status}.")
    v.applicant_message = body.applicant_message
    r = Review(version_id=v.id, reviewer_id=p.user.id, decision=Decision.REJECTED, applicant_message=body.applicant_message,
               internal_note=body.internal_note, check_status_at_decision=v.check_status)
    r._reason_code = body.reason_code
    _finish_decision(db, p, app, v, ReviewStatus.REJECTED, r,
                     ("Application not approved", body.applicant_message), final=True)
    return Msg(detail="Rejected")


@router.post("/applications/{app_id}/request-changes", response_model=Msg)
def request_changes(app_id: str, body: ChangesIn, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app, v = _locked_current(db, p, app_id, body.version_id, body.row_version)
    if v.review_status != ReviewStatus.PENDING_REVIEW and v.processing_status not in (
            ProcessingStatus.FAILED, ProcessingStatus.NEEDS_REUPLOAD):
        raise HTTPException(409, f"Cannot request changes from status {v.review_status}.")
    items = [i.model_dump() for i in body.items]
    v.applicant_message, v.requested_items = body.applicant_message, items
    _finish_decision(db, p, app, v, ReviewStatus.CHANGES_REQUESTED,
                     Review(version_id=v.id, reviewer_id=p.user.id, decision=Decision.CHANGES_REQUESTED,
                            applicant_message=body.applicant_message, internal_note=body.internal_note,
                            requested_items=items, check_status_at_decision=v.check_status),
                     ("Northbridge requested changes", body.applicant_message + " Requested: " +
                      "; ".join(f"{i['category'].replace('_', ' ').title()}: {i['instruction']}" for i in items)),
                     final=False)
    return Msg(detail="Changes requested")


@router.post("/applications/{app_id}/notes", response_model=Msg)
def add_note(app_id: str, body: NoteIn, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    if body.version_id not in {x.id for x in app.versions}:
        raise HTTPException(404, "Version not found")
    db.add(Review(version_id=body.version_id, reviewer_id=p.user.id, decision=Decision.NOTE, internal_note=body.note))
    audit(db, action="review.note_added", target_type="application_version", target_id=body.version_id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return Msg(detail="Note added")


@router.post("/applications/{app_id}/reprocess", response_model=Msg, summary="Re-run processing (e.g. after a failure)")
def reprocess(app_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    v = current_version(app)
    if v.state != VersionState.SUBMITTED or v.review_status in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
        raise HTTPException(409, "Only submitted, undecided versions can be reprocessed.")
    if v.processing_status in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
        raise HTTPException(409, "Processing is already in progress.")
    enqueue_processing(db, v)
    audit(db, action="application.reprocess_requested", target_type="application_version", target_id=v.id,
          actor=p.user, organization_id=app.organization_id, application_id=app.id, attempt=v.processing_attempt)
    db.commit()
    return Msg(detail="Reprocessing queued")


@router.get("/applications/{app_id}/export.csv", summary="Metadata-only report (no PII)")
def export(app_id: str, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    app = _app_for_staff(db, p, app_id)
    v = current_version(app)
    data = serialize_version(db, v, staff=False)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "version", "processing_status", "check_status", "review_status", "submitted_at"])
    w.writerow([app.reference, v.version_number, v.processing_status, v.check_status, v.review_status, v.submitted_at])
    w.writerow([])
    w.writerow(["check_type", "status", "code", "explanation"])
    for c in data["checks"]:
        w.writerow([c["check_type"], c["status"], c["code"], c["explanation"]])
    w.writerow([])
    w.writerow(["document_category", "format", "pages_processed", "processing_status", "quality_flags"])
    for d in data["documents"]:
        w.writerow([d["category"], d["file_format"], " ".join(map(str, d["pages_processed"] or [])),
                    d["processing_status"], " ".join(d["quality_flags"])])
    audit(db, action="application.exported", target_type="application", target_id=app.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{app.reference}-metadata.csv"',
                             "Cache-Control": "no-store"})


# ============================================================================ admin
def _admin_org(p: Principal) -> str:
    orgs = sorted(p.org_ids())
    if not orgs:
        raise HTTPException(403, "No organisation membership")
    return orgs[0]


@admin.get("/staff")
def list_staff(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    org = _admin_org(p)
    rows = db.execute(select(User, StaffMembership).join(StaffMembership).where(StaffMembership.organization_id == org)).all()
    return [{"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role, "is_active": u.is_active,
             "has_password": u.password_hash is not None, "created_at": u.created_at} for u, _ in rows]


@admin.post("/staff", status_code=201)
def create_staff(body: StaffCreateIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    from app.api.auth import send_password_setup

    org = _admin_org(p)
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "A user with this email already exists.")
    u = User(email=body.email.lower(), full_name=body.full_name, role=body.role, password_hash=None)
    db.add(u)
    db.flush()
    db.add(StaffMembership(user_id=u.id, organization_id=org, role=body.role))
    send_password_setup(db, u, purpose_text="set your Northbridge staff password")
    audit(db, action="staff.created", target_type="user", target_id=u.id, actor=p.user, organization_id=org,
          target_role=body.role)
    db.commit()
    return {"id": u.id, "detail": "Invitation sent. The new staff member sets their own password via the emailed link."}


@admin.patch("/staff/{user_id}")
def update_staff(user_id: str, body: StaffUpdateIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    org = _admin_org(p)
    m = db.scalar(select(StaffMembership).where(StaffMembership.user_id == user_id, StaffMembership.organization_id == org))
    if m is None:
        raise HTTPException(404, "Staff member not found")
    u = db.get(User, user_id)
    if u.id == p.user.id and (body.is_active is False or body.role == "reviewer"):
        raise HTTPException(409, "You cannot demote or deactivate yourself.")
    if body.role:
        u.role = m.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            from app.auth.sessions import revoke_all_sessions
            revoke_all_sessions(db, u.id)
    audit(db, action="staff.updated", target_type="user", target_id=u.id, actor=p.user, organization_id=org,
          target_role=u.role)
    db.commit()
    return {"detail": "Updated"}


@admin.get("/policy")
def read_policy(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    pol = get_policy(db, _admin_org(p))
    db.commit()
    return {**serialize_policy(pol), "notice": "Prototype product policy - not a legal determination."}


@admin.put("/policy")
def write_policy(body: PolicyIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    if body.required_salary_slips > body.max_salary_slips:
        raise HTTPException(422, "Required salary slips cannot exceed the maximum.")
    org = _admin_org(p)
    pol = get_policy(db, org)
    for k, val in body.model_dump().items():
        setattr(pol, k, val)
    pol.updated_by = p.user.id
    audit(db, action="policy.updated", target_type="org_policy", target_id=pol.id, actor=p.user, organization_id=org,
          **body.model_dump())
    db.commit()
    return serialize_policy(pol)


@admin.get("/metrics")
def metrics(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    jobs = dict(db.execute(select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)).all())
    errors = dict(db.execute(select(ProcessingJob.last_error_class, func.count()).where(
        ProcessingJob.last_error_class.is_not(None)).group_by(ProcessingJob.last_error_class)).all())
    stuck = db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.status == JobStatus.RUNNING, ProcessingJob.lease_expires_at < datetime.now(UTC)))
    from app.models import ExtractionResult
    lat = db.scalar(select(func.avg(ExtractionResult.llm_latency_ms)))
    checks = dict(db.execute(select(ApplicationVersion.check_status, func.count()).where(
        ApplicationVersion.check_status.is_not(None)).group_by(ApplicationVersion.check_status)).all())
    docs = dict(db.execute(select(Document.processing_status, func.count()).group_by(Document.processing_status)).all())
    return {"jobs_by_status": jobs, "job_error_classes": errors, "expired_leases": stuck,
            "avg_llm_latency_ms": round(float(lat), 1) if lat else None, "check_status_counts": checks,
            "document_status_counts": docs, "purged_documents": db.scalar(
                select(func.count()).select_from(Document).where(Document.purged_at.is_not(None)))}


@admin.get("/identity-conflicts")
def list_conflicts(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    fps = db.scalars(select(IdentityClaim.fingerprint).where(IdentityClaim.status == ClaimStatus.CONFLICT).distinct()).all()
    groups = []
    for fp in fps:
        claims = db.scalars(select(IdentityClaim).where(IdentityClaim.fingerprint == fp)).all()
        names = [normalize_name(decrypt_str(c.name_enc, "identity-name") or "") for c in claims]
        apps = {c.application_id: db.get(Application, c.application_id) for c in claims}
        groups.append({
            "group_ref": claims[0].id, "id_type": claims[0].id_type, "masked_value": claims[0].masked_value,
            "names_agree_across_claims": len(set(names)) == 1,
            "claims": [{"claim_id": c.id, "status": c.status, "application_reference": apps[c.application_id].reference,
                        "application_id": c.application_id, "created_at": c.created_at,
                        "applicant_masked": mask_name(db.get(User, c.user_id).full_name)} for c in claims],
        })
    return groups


@admin.post("/identity-conflicts/resolve")
def resolve(body: ResolveConflictIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    ref = db.get(IdentityClaim, body.fingerprint_ref)
    if ref is None:
        raise HTTPException(404, "Conflict not found")
    try:
        claims = identity_svc.resolve_conflict(db, fingerprint=ref.fingerprint, holder_claim_id=body.holder_claim_id,
                                               admin_id=p.user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    requeued = 0
    for c in claims:
        v = db.get(ApplicationVersion, c.version_id)
        if v and v.is_current and v.state == VersionState.SUBMITTED and v.review_status not in (
                ReviewStatus.APPROVED, ReviewStatus.REJECTED) and v.processing_status not in (
                ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
            enqueue_processing(db, v)
            requeued += 1
        db.add(Review(version_id=c.version_id, reviewer_id=p.user.id, decision=Decision.NOTE,
                      internal_note="Identity conflict resolution: " + body.note))
        audit(db, action="identity.conflict_resolved", target_type="identity_claim", target_id=c.id, actor=p.user,
              application_id=c.application_id, resolution="HOLDER" if c.id == body.holder_claim_id else "REJECTED",
              id_type=c.id_type)
    db.commit()
    return {"detail": "Resolved", "claims": len(claims), "reprocessing_queued": requeued}



@router.post("/applications/{app_id}/require-reverification", response_model=Msg)
def require_reverification(app_id: str, body: LifecycleIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    if len(body.applicant_message.strip()) < 10:
        raise HTTPException(422, "Enter a reason of at least 10 characters.")
    app, old = _locked_current(db, p, app_id, body.version_id, body.row_version)
    user = db.get(User, app.applicant_id)
    if not user.is_active or old.review_status != ReviewStatus.APPROVED:
        raise HTTPException(409, "Only an active, approved account can be required to verify again.")
    old.is_current = False
    old.state = VersionState.SUPERSEDED
    old.row_version += 1
    db.flush()
    items = [{"category": category, "instruction": "Upload a fresh, current document for verification."}
             for category in ("AADHAAR", "PAN", "SALARY_SLIP")]
    new = ApplicationVersion(application_id=app.id, version_number=old.version_number + 1,
        is_current=True, parent_version_id=old.id, requested_items=items,
        applicant_message="Your previous KYC approval has been cancelled. Please upload fresh documents and submit them for verification. " + body.applicant_message)
    db.add(new)
    db.flush()
    # Historical approval remains auditable; its identity association is no longer confirmed.
    for claim in db.scalars(select(IdentityClaim).where(IdentityClaim.version_id == old.id)):
        if claim.status == ClaimStatus.CONFIRMED:
            claim.status = ClaimStatus.UNVERIFIED
        confirmed = db.scalar(select(ConfirmedIdentity).where(ConfirmedIdentity.fingerprint == claim.fingerprint,
                                                             ConfirmedIdentity.user_id == app.applicant_id))
        if confirmed:
            db.delete(confirmed)
    emit(db, new, Stage.UPLOAD_RECEIVED, "info", "Previous approval cancelled by an administrator. Fresh documents required.", commit=False)
    notify(db, app.applicant_id, "KYC_REVERIFICATION_REQUIRED", "KYC approval cancelled — verify again", new.applicant_message, app.id)
    audit(db, action="application.approval_revoked", target_type="application_version", target_id=old.id,
          actor=p.user, organization_id=app.organization_id, application_id=app.id,
          new_version_id=new.id, reason=body.applicant_message)
    db.commit()
    return Msg(detail="Approval cancelled. Fresh documents are required.")


@router.post("/applications/{app_id}/close-account", response_model=Msg)
def close_applicant_account(app_id: str, body: LifecycleIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    from app.auth.sessions import revoke_all_sessions
    if len(body.applicant_message.strip()) < 10:
        raise HTTPException(422, "Enter a reason of at least 10 characters.")
    app, version = _locked_current(db, p, app_id, body.version_id, body.row_version)
    user = db.get(User, app.applicant_id)
    if user.role != Role.APPLICANT:
        raise HTTPException(409, "Only applicant accounts can be closed here.")
    # Account closure affects all its applications; require authority over every organization.
    apps = db.scalars(select(Application).where(Application.applicant_id == user.id)).all()
    if any(a.organization_id not in p.org_ids() for a in apps):
        raise HTTPException(403, "Account closure requires access to all of this applicant's organizations.")
    if not user.is_active:
        raise HTTPException(409, "This account is already closed.")
    for application in apps:
        current = current_version(application)
        if current.processing_status in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
            raise HTTPException(409, "Wait for or cancel document processing before closing this account.")
    user.is_active = False
    revoke_all_sessions(db, user.id)
    for application in apps:
        current = current_version(application)
        previous = current.review_status
        current.review_status = ReviewStatus.REJECTED
        current.decided_at = datetime.now(UTC)
        current.applicant_message = "Your account has been closed by an administrator. " + body.applicant_message
        current.requested_items = None
        current.row_version += 1
        emit(db, current, Stage.DECISION, "info", "Account closed by an administrator.", commit=False)
        notify(db, user.id, "ACCOUNT_CLOSED", "Account closed", current.applicant_message, application.id)
        audit(db, action="applicant.account_closed", target_type="user", target_id=user.id, actor=p.user,
              application_id=application.id, organization_id=application.organization_id,
              from_status=previous, reason=body.applicant_message)
    # Keep identifiers reserved to prevent closure from enabling cross-account reuse.
    db.commit()
    return Msg(detail="Account closed and all sessions revoked. Review history retained.")


@router.get("/processing-schema")
def processing_schema(p: Principal = Depends(require_staff)):
    """The schema definition only; never includes an applicant's raw OCR text."""
    from app.pipeline.ocr_schema import OcrResultSchema
    return OcrResultSchema.model_json_schema()
