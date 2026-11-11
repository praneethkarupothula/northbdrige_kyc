from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.deps import Principal, authorize_application, current_principal, require_applicant
from app.auth.sessions import load_session
from app.core.config import get_settings
from app.core.crypto import content_fingerprint, encrypt_str
from app.core.db import get_db, get_sessionmaker, is_postgres
from app.models import (
    Application, ApplicationVersion, Document, DocumentCategory, JobStatus, Notification, PanStatus,
    ProcessingEvent, ProcessingJob, ProcessingStatus, ReviewStatus, Stage, VersionState,
)
from app.pipeline.validation import FileRejected, validate_upload
from app.schemas.api import ApplicationOut, DraftUpdateIn, Msg, RowVersionIn, SubmitIn
from app.services.audit import audit
from app.services.events import emit
from app.services.policy import default_org, get_policy
from app.services.views import (
    CATEGORY_LABEL, current_version, history, serialize_event, serialize_policy, serialize_version,
)
from app.storage.blob import get_store
from app.workers import queue

router = APIRouter(prefix="/v1", tags=["applicant"])


# ----------------------------------------------------------------------------- helpers
def _app_out(db: Session, app: Application, *, staff: bool = False) -> dict:
    v = current_version(app)
    return {"id": app.id, "reference": app.reference, "created_at": app.created_at,
            "current": serialize_version(db, v, staff=staff), "history": history(app),
            "policy": serialize_policy(get_policy(db, app.organization_id))}


def _own_app(db: Session, p: Principal, app_id: str) -> Application:
    return authorize_application(p, db.get(Application, app_id))


def _lock_version(db: Session, version_id: str) -> ApplicationVersion:
    q = select(ApplicationVersion).where(ApplicationVersion.id == version_id)
    if is_postgres(db):
        q = q.with_for_update()
    return db.scalar(q)


def _draft(db: Session, app: Application) -> ApplicationVersion:
    v = _lock_version(db, current_version(app).id)
    if v.state != VersionState.DRAFT:
        raise HTTPException(409, "This application has been submitted and cannot be edited in place. "
                                 "Start a revision to make corrections.")
    return v


# ----------------------------------------------------------------------------- drafts
@router.post("/applications", response_model=ApplicationOut, summary="Create or resume the applicant's draft")
def create_or_resume(p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    org = default_org(db)
    app = db.scalar(select(Application).where(Application.applicant_id == p.user.id,
                                              Application.organization_id == org.id))
    if app is None:
        pol = get_policy(db, org.id)
        app = Application(applicant_id=p.user.id, organization_id=org.id,
                          reference="NB-" + secrets.token_hex(4).upper())
        db.add(app)
        db.flush()
        db.add(ApplicationVersion(application_id=app.id, version_number=1, is_current=True,
                                  pan_status=PanStatus.NOT_REQUESTED if pol.pan_policy == "not_requested"
                                  else PanStatus.PENDING))
        audit(db, action="application.created", target_type="application", target_id=app.id, actor=p.user,
              organization_id=org.id, application_id=app.id)
        db.commit()
        db.refresh(app)
    return _app_out(db, app)


@router.get("/applications/mine", summary="Applicant's applications (empty list if none yet)")
def my_applications(p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    apps = db.scalars(select(Application).where(Application.applicant_id == p.user.id)).all()
    return [_app_out(db, a) for a in apps]


@router.get("/applications/{app_id}", response_model=ApplicationOut)
def get_application(app_id: str, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    return _app_out(db, _own_app(db, p, app_id))


@router.get("/applications/{app_id}/versions/{version_id}", summary="A past version (submission history)")
def get_version(app_id: str, version_id: str, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    v = next((v for v in app.versions if v.id == version_id), None)
    if v is None:
        raise HTTPException(404, "Version not found")
    return serialize_version(db, v, staff=False)


@router.patch("/applications/{app_id}/draft", response_model=ApplicationOut)
def update_draft(app_id: str, body: DraftUpdateIn, p: Principal = Depends(require_applicant),
                 db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    v = _draft(db, app)
    pol = get_policy(db, app.organization_id)
    if body.pan_choice is not None:
        if pol.pan_policy == "not_requested":
            raise HTTPException(422, "PAN is not requested under the current policy.")
        if body.pan_choice == "UNAVAILABLE":
            if pol.pan_policy == "required":
                raise HTTPException(422, "The current policy requires a PAN card.")
            if any(d.category == DocumentCategory.PAN for d in v.documents):
                raise HTTPException(409, "Remove the uploaded PAN card first.")
            v.pan_status = PanStatus.UNAVAILABLE
        else:
            v.pan_status = PanStatus.SUPPLIED if any(d.category == DocumentCategory.PAN for d in v.documents) \
                else PanStatus.PENDING
        v.row_version += 1
    db.commit()
    return _app_out(db, app)


# ----------------------------------------------------------------------------- uploads
def _store_document(db: Session, v: ApplicationVersion, category: str, data: bytes, filename: str | None) -> Document:
    vf = validate_upload(data)
    store = get_store()
    key = store.put(data)
    doc = Document(version_id=v.id, category=category, storage_key=key, file_format=vf.file_format,
                   size_bytes=len(data), content_fingerprint=content_fingerprint(data),
                   original_filename_enc=encrypt_str((filename or "upload")[:200], "filename"),
                   page_count=vf.page_count)
    db.add(doc)
    db.flush()
    return doc


@router.post("/applications/{app_id}/documents", status_code=201, summary="Upload or replace a document")
def upload_document(
    app_id: str,
    request: Request,
    category: DocumentCategory = Form(...),
    file: UploadFile = File(...),
    replace_document_id: str | None = Form(None),
    p: Principal = Depends(require_applicant),
    db: Session = Depends(get_db),
):
    s = get_settings()
    app = _own_app(db, p, app_id)
    v = _draft(db, app)
    pol = get_policy(db, app.organization_id)
    data = file.file.read(s.max_file_bytes + 1)
    max_bytes = min(s.max_file_bytes, pol.max_file_mb * 1024 * 1024)
    existing = [d for d in v.documents if d.purged_at is None]
    replacing = None
    if replace_document_id:
        replacing = next((d for d in existing if d.id == replace_document_id), None)
        if replacing is None:
            raise HTTPException(404, "Document to replace not found")
        if replacing.category != category:
            raise HTTPException(422, "A replacement must be the same document type.")
        existing = [d for d in existing if d.id != replacing.id]
    same = [d for d in existing if d.category == category]
    if category == DocumentCategory.PAN and pol.pan_policy == "not_requested":
        raise HTTPException(422, "PAN is not requested under the current policy.")
    if category == DocumentCategory.PAN and v.pan_status == PanStatus.UNAVAILABLE and pol.pan_policy != "required":
        raise HTTPException(409, "You indicated you do not have a PAN card. Change that choice first.")
    if category == DocumentCategory.BANK_STATEMENT and not pol.accept_bank_statement:
        raise HTTPException(422, "Bank statements are not accepted under the current policy.")
    limit = {DocumentCategory.AADHAAR: 1, DocumentCategory.PAN: 1, DocumentCategory.BANK_STATEMENT: 1,
             DocumentCategory.SALARY_SLIP: pol.max_salary_slips}[category]
    if len(same) >= limit:
        raise HTTPException(409, f"You can upload at most {limit} {CATEGORY_LABEL[category].lower()} file(s). "
                                 "Replace or remove an existing one.")
    if len(existing) >= s.max_files_per_version:
        raise HTTPException(409, f"At most {s.max_files_per_version} files per application.")
    try:
        doc = _store_document(db, v, category, data, file.filename) if len(data) <= max_bytes else None
        if doc is None:
            raise FileRejected("FILE_TOO_LARGE", f"Files must be {max_bytes // (1024 * 1024)} MiB or smaller.")
    except FileRejected as fr:
        db.rollback()
        raise HTTPException(422, {"code": fr.code, "message": fr.message}) from None
    if replacing is not None:
        _delete_doc(db, replacing)
    if category == DocumentCategory.PAN:
        v.pan_status = PanStatus.SUPPLIED
    duplicate_of = next((d.id for d in existing if d.content_fingerprint == doc.content_fingerprint), None)
    v.row_version += 1
    emit(db, v, Stage.UPLOAD_RECEIVED, "succeeded", f"{CATEGORY_LABEL[category]} received "
         f"({doc.file_format.upper()}, {doc.page_count} page(s)).", document_id=doc.id, commit=False)
    audit(db, action="document.uploaded", target_type="document", target_id=doc.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id, category=category)
    db.commit()
    return {"document_id": doc.id, "duplicate_of": duplicate_of,
            "warning": "This file is identical to one you already uploaded." if duplicate_of else None,
            "application": _app_out(db, app)}


def _delete_doc(db: Session, doc: Document) -> None:
    if doc.storage_key:
        get_store().delete(doc.storage_key)
    db.delete(doc)


@router.delete("/applications/{app_id}/documents/{doc_id}", response_model=ApplicationOut)
def delete_document(app_id: str, doc_id: str, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    v = _draft(db, app)
    doc = next((d for d in v.documents if d.id == doc_id), None)
    if doc is None:
        raise HTTPException(404, "Document not found")
    category = doc.category
    _delete_doc(db, doc)
    db.flush()
    if category == DocumentCategory.PAN and v.pan_status == PanStatus.SUPPLIED:
        v.pan_status = PanStatus.PENDING
    v.row_version += 1
    audit(db, action="document.deleted", target_type="document", target_id=doc_id, actor=p.user,
          application_id=app.id, category=category)
    db.commit()
    db.refresh(v)
    return _app_out(db, app)


# ----------------------------------------------------------------------------- submission
def missing_requirements(v: ApplicationVersion, pol) -> list[str]:
    cats = [d.category for d in v.documents if d.purged_at is None]
    missing = []
    if DocumentCategory.AADHAAR not in cats:
        missing.append("Upload your Aadhaar card.")
    n = cats.count(DocumentCategory.SALARY_SLIP)
    if n < pol.required_salary_slips:
        missing.append(f"Upload at least {pol.required_salary_slips} salary slip(s).")
    if pol.pan_policy == "required" and DocumentCategory.PAN not in cats:
        missing.append("Upload your PAN card (required by the current policy).")
    if pol.pan_policy == "optional" and v.pan_status == PanStatus.PENDING:
        missing.append("Upload your PAN card or tick “I do not have a PAN card”.")
    return missing


def enqueue_processing(db: Session, v: ApplicationVersion) -> None:
    v.processing_attempt += 1
    v.processing_status = ProcessingStatus.QUEUED
    v.check_status = None
    v.review_status = ReviewStatus.NOT_READY
    v.row_version += 1
    queue.enqueue(db, "process_version", {"version_id": v.id, "attempt": v.processing_attempt},
                  f"process_version:{v.id}:{v.processing_attempt}")
    emit(db, v, Stage.UPLOAD_RECEIVED, "info", "Queued for processing.", commit=False)


@router.post("/applications/{app_id}/submit", response_model=ApplicationOut)
def submit(app_id: str, body: SubmitIn, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    v = _draft(db, app)
    if body.row_version != v.row_version:
        raise HTTPException(409, "This draft changed in another tab. Reload and try again.")
    if not body.consent:
        raise HTTPException(422, "Please confirm the privacy and consent statement before submitting.")
    pol = get_policy(db, app.organization_id)
    missing = missing_requirements(v, pol)
    if missing:
        raise HTTPException(422, {"code": "MISSING_DOCUMENTS", "message": " ".join(missing), "items": missing})
    if pol.pan_policy == "not_requested":
        v.pan_status = PanStatus.NOT_REQUESTED
    v.state = VersionState.SUBMITTED
    v.consent_at = v.submitted_at = datetime.now(UTC)
    enqueue_processing(db, v)
    audit(db, action="application.submitted", target_type="application_version", target_id=v.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id, version_number=v.version_number)
    db.commit()
    return _app_out(db, app)


@router.post("/applications/{app_id}/cancel", response_model=ApplicationOut, summary="Cancel processing safely")
def cancel(app_id: str, body: RowVersionIn, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    v = _lock_version(db, current_version(app).id)
    if v.processing_status not in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
        raise HTTPException(409, "Nothing is being processed.")
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.idempotency_key ==
                                                  f"process_version:{v.id}:{v.processing_attempt}")).all()
    for j in jobs:
        if j.status == JobStatus.QUEUED:
            j.status = JobStatus.CANCELLED
            v.state, v.processing_status = VersionState.DRAFT, ProcessingStatus.NOT_STARTED
            v.row_version += 1
            emit(db, v, Stage.UPLOAD_RECEIVED, "info", "Processing was cancelled. The application is a draft again.",
                 commit=False)
        elif j.status == JobStatus.RUNNING:
            j.cancel_requested = True
            emit(db, v, Stage.UPLOAD_RECEIVED, "info", "Cancellation requested; stopping after the current step.",
                 commit=False)
    audit(db, action="application.cancel_requested", target_type="application_version", target_id=v.id,
          actor=p.user, application_id=app.id)
    db.commit()
    return _app_out(db, app)


@router.post("/applications/{app_id}/revise", response_model=ApplicationOut,
             summary="Start a controlled resubmission (new version; earlier results become stale)")
def revise(app_id: str, body: RowVersionIn, p: Principal = Depends(require_applicant), db: Session = Depends(get_db)):
    app = _own_app(db, p, app_id)
    old = _lock_version(db, current_version(app).id)
    if body.row_version != old.row_version:
        raise HTTPException(409, "This application changed. Reload and try again.")
    if old.state == VersionState.DRAFT:
        raise HTTPException(409, "You already have an editable draft.")
    if old.review_status == ReviewStatus.APPROVED:
        raise HTTPException(409, "This application is already approved.")
    if old.processing_status in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
        raise HTTPException(409, "Cancel processing before starting a revision.")
    old.is_current = False
    old.state = VersionState.SUPERSEDED
    old.row_version += 1
    db.flush()
    new = ApplicationVersion(application_id=app.id, version_number=old.version_number + 1, is_current=True,
                             pan_status=old.pan_status, parent_version_id=old.id,
                             requested_items=old.requested_items, applicant_message=None)
    db.add(new)
    db.flush()
    store = get_store()
    for d in old.documents:
        if d.purged_at is None and d.storage_key:
            data = store.get(d.storage_key)
            db.add(Document(version_id=new.id, category=d.category, storage_key=store.put(data),
                            file_format=d.file_format, size_bytes=d.size_bytes,
                            content_fingerprint=d.content_fingerprint, original_filename_enc=d.original_filename_enc,
                            page_count=d.page_count))
    emit(db, new, Stage.UPLOAD_RECEIVED, "info",
         f"Revision {new.version_number} started from version {old.version_number}. Earlier results are no longer used.",
         commit=False)
    audit(db, action="application.revision_started", target_type="application_version", target_id=new.id,
          actor=p.user, application_id=app.id, version_number=new.version_number)
    db.commit()
    db.refresh(app)
    return _app_out(db, app)


# ----------------------------------------------------------------------------- progress
@router.get("/applications/{app_id}/events", summary="Polling fallback for progress events")
def list_events(app_id: str, after: int = 0, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    app = authorize_application(p, db.get(Application, app_id))
    rows = db.scalars(select(ProcessingEvent).where(ProcessingEvent.application_id == app.id,
                                                    ProcessingEvent.id > after)
                      .order_by(ProcessingEvent.id).limit(500)).all()
    v = current_version(app)
    return {"events": [serialize_event(e) for e in rows], "status": _status_snapshot(v)}


def _status_snapshot(v: ApplicationVersion) -> dict:
    return {"version_id": v.id, "row_version": v.row_version, "processing_status": v.processing_status,
            "check_status": v.check_status, "review_status": v.review_status, "state": v.state}


def _sse_auth(request: Request, app_id: str) -> bool:
    with get_sessionmaker()() as db:
        loaded = load_session(db, request)
        if loaded is None:
            return False
        sess, user = loaded
        try:
            authorize_application(Principal(user=user, session=sess), db.get(Application, app_id))
        except HTTPException:
            return False
        return True


def _sse_fetch(app_id: str, last_id: int):
    with get_sessionmaker()() as db:
        rows = db.scalars(select(ProcessingEvent).where(ProcessingEvent.application_id == app_id,
                                                        ProcessingEvent.id > last_id)
                          .order_by(ProcessingEvent.id).limit(200)).all()
        app = db.get(Application, app_id)
        return [serialize_event(e) for e in rows], _status_snapshot(current_version(app))


@router.get("/applications/{app_id}/events/stream", summary="Server-Sent Events progress stream",
            response_class=StreamingResponse)
async def stream_events(app_id: str, request: Request, after: int = 0):
    """Authenticated by the HttpOnly session cookie - no secrets in the URL. Supports Last-Event-ID."""
    if not await run_in_threadpool(_sse_auth, request, app_id):
        raise HTTPException(404, "Application not found")
    try:
        last = int(request.headers.get("last-event-id") or after or 0)
    except ValueError:
        last = 0

    async def gen():
        nonlocal last
        yield "retry: 3000\n\n"
        last_status = None
        ticks = 0
        max_ticks = get_settings().sse_max_seconds
        while ticks < max_ticks:  # then the browser reconnects with Last-Event-ID
            if await request.is_disconnected():
                return
            if ticks and ticks % 30 == 0 and not await run_in_threadpool(_sse_auth, request, app_id):
                yield "event: auth\ndata: {\"detail\": \"session ended\"}\n\n"
                return
            events, snap = await run_in_threadpool(_sse_fetch, app_id, last)
            for e in events:
                last = e["id"]
                yield f"id: {e['id']}\nevent: progress\ndata: {json.dumps(e, default=str)}\n\n"
            if snap != last_status:
                last_status = snap
                yield f"event: status\ndata: {json.dumps(snap)}\n\n"
            elif ticks % 15 == 0:
                yield ": keep-alive\n\n"
            ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == p.user.id)
                      .order_by(Notification.created_at.desc()).limit(50)).all()
    return {"unread": sum(1 for n in rows if n.read_at is None),
            "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "read": n.read_at is not None,
                       "application_id": n.application_id, "created_at": n.created_at} for n in rows]}


@router.post("/notifications/{nid}/read", response_model=Msg)
def mark_read(nid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or n.user_id != p.user.id:
        raise HTTPException(404, "Not found")
    n.read_at = n.read_at or datetime.now(UTC)
    db.commit()
    return Msg(detail="ok")


@router.post("/notifications/read-all", response_model=Msg)
def mark_all_read(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == p.user.id, Notification.read_at.is_(None))
               .values(read_at=datetime.now(UTC)))
    db.commit()
    return Msg(detail="ok")


# ----------------------------------------------------------------------------- compatibility
@router.post("/extract", status_code=202, tags=["compatibility"],
             summary="Authenticated compatibility endpoint: adds a file to your draft (no anonymous processing)")
def extract_compat(
    request: Request,
    category: DocumentCategory = Form(...),
    file: UploadFile = File(...),
    p: Principal = Depends(require_applicant),
    db: Session = Depends(get_db),
):
    app_out = create_or_resume(p, db)
    res = upload_document(app_out["id"], request, category, file, None, p, db)
    return {"application_id": app_out["id"], "document_id": res["document_id"],
            "next": "Submit the application to start processing; results arrive via the progress stream."}

