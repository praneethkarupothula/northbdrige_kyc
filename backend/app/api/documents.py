"""Authorised document evidence. No permanent or public URLs exist; every read is checked and
staff reads are audited. Page previews are redacted by default."""

from __future__ import annotations

import io

import pymupdf
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from app.auth.deps import Principal, authorize_application, current_principal
from app.core.crypto import decrypt_json
from app.core.db import get_db
from app.models import Application, ApplicationVersion, Document, Role
from app.services.audit import audit
from app.services.views import latest_results
from app.storage.blob import get_store

router = APIRouter(prefix="/v1/documents", tags=["documents"])

MASKED_ENTITIES = {"AADHAAR", "PAN", "DOB", "ACCOUNT", "PHONE", "EMAIL", "ADDRESS", "IFSC", "ID", "EMPID"}
PAD_PX = 6  # at least five pixels of padding around every redaction
NO_STORE = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox", "Cross-Origin-Resource-Policy": "same-origin"}


def _load(db: Session, p: Principal, doc_id: str) -> tuple[Document, ApplicationVersion, Application]:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    v = db.get(ApplicationVersion, doc.version_id)
    app = authorize_application(p, db.get(Application, v.application_id))
    if doc.purged_at is not None or not doc.storage_key:
        raise HTTPException(410, "This file was deleted under the retention policy.")
    return doc, v, app


def can_reveal(p: Principal, v: ApplicationVersion, app: Application) -> bool:
    if p.role == Role.APPLICANT:
        return app.applicant_id == p.user.id
    if p.role == Role.ADMIN:
        return True
    return p.role == Role.REVIEWER and v.assigned_reviewer_id == p.user.id


def _render_page(data: bytes, fmt: str, page: int) -> Image.Image:
    if fmt == "pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            if page < 1 or page > doc.page_count:
                raise HTTPException(404, "Page not found")
            pix = doc[page - 1].get_pixmap(dpi=110)
            return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        finally:
            doc.close()
    if page != 1:
        raise HTTPException(404, "Page not found")
    with Image.open(io.BytesIO(data)) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600))
    return img


def redact(img: Image.Image, boxes: list[dict], page: int) -> tuple[Image.Image, int]:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    n = 0
    for b in boxes:
        if b.get("page") != page or b.get("entity") not in MASKED_ENTITIES:
            continue
        x0, y0, x1, y1 = b["bbox"]
        draw.rectangle([max(0, x0 * w - PAD_PX), max(0, y0 * h - PAD_PX), min(w, x1 * w + PAD_PX),
                        min(h, y1 * h + PAD_PX)], fill=(0, 0, 0))
        n += 1
    return img, n


@router.get("/{doc_id}/pages/{page}", summary="Rendered page preview (redacted unless reveal=true)",
            responses={200: {"content": {"image/png": {}}}})
def page_preview(doc_id: str, page: int, reveal: bool = False, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    doc, v, app = _load(db, p, doc_id)
    if reveal and not can_reveal(p, v, app):
        raise HTTPException(403, "Unmasked view requires the assigned reviewer or an administrator.")
    img = _render_page(get_store().get(doc.storage_key), doc.file_format, page)
    boxes = (doc.quality or {}).get("redactions", [])
    redacted = 0
    if not reveal:
        img, redacted = redact(img, boxes, page)
    if p.is_staff:
        audit(db, action="document.preview_unmasked" if reveal else "document.preview", target_type="document",
              target_id=doc.id, actor=p.user, organization_id=app.organization_id, application_id=app.id,
              page=page, reveal=reveal)
        db.commit()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    headers = dict(NO_STORE)
    headers["X-Redactions-Applied"] = str(redacted)
    headers["X-Image-Size"] = f"{img.size[0]}x{img.size[1]}"
    return Response(buf.getvalue(), media_type="image/png", headers=headers)


@router.get("/{doc_id}/original", summary="Original file (privileged; audited)")
def original(doc_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    doc, v, app = _load(db, p, doc_id)
    if not can_reveal(p, v, app):
        raise HTTPException(403, "The original file requires the assigned reviewer or an administrator.")
    audit(db, action="document.original_viewed", target_type="document", target_id=doc.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    media = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png"}[doc.file_format]
    headers = dict(NO_STORE)
    headers["Content-Disposition"] = f'inline; filename="document-{doc.id[:8]}.{doc.file_format}"'
    return Response(get_store().get(doc.storage_key), media_type=media, headers=headers)


@router.post("/{doc_id}/reveal", summary="Reveal exact extracted values (privileged; audited)")
def reveal_fields(doc_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    v = db.get(ApplicationVersion, doc.version_id)
    app = authorize_application(p, db.get(Application, v.application_id))
    if not can_reveal(p, v, app):
        raise HTTPException(403, "Revealing values requires the assigned reviewer or an administrator.")
    res = latest_results(db, v).get(doc.id)
    if res is None:
        raise HTTPException(404, "No extraction result for this document")
    audit(db, action="document.fields_revealed", target_type="document", target_id=doc.id, actor=p.user,
          organization_id=app.organization_id, application_id=app.id)
    db.commit()
    return {"document_id": doc.id, "fields": decrypt_json(res.fields_enc, "extraction-fields")}
