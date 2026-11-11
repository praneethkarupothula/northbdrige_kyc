"""Configurable retention (a prototype product policy - not a legal retention requirement).

Default: review evidence is kept until a decision, and original files are deleted N days
(default 7) after the decision. Extracted values remain encrypted in the database for the
audit trail. Deleting a file removes it from primary storage only; backups/snapshots of the
storage volume or database are outside this application's control (see docs/SECURITY.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationVersion, Document
from app.services.audit import audit
from app.services.policy import get_policy
from app.storage.blob import get_store


def schedule_purge_for_application(db: Session, application: Application, decided_at: datetime) -> None:
    pol = get_policy(db, application.organization_id)
    decided_at = decided_at.replace(tzinfo=UTC) if decided_at.tzinfo is None else decided_at.astimezone(UTC)
    when = decided_at + timedelta(days=pol.retention_days_after_decision)
    for v in db.scalars(select(ApplicationVersion).where(ApplicationVersion.application_id == application.id)):
        for d in v.documents:
            # SQLite returns timezone-naive timestamps; stored dates are UTC.
            existing = d.purge_after
            if existing is not None:
                existing = existing.replace(tzinfo=UTC) if existing.tzinfo is None else existing.astimezone(UTC)
            if d.purged_at is None and (existing is None or existing > when):
                d.purge_after = when


def sweep(db: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    store = get_store()
    due = db.scalars(select(Document).where(Document.purge_after.is_not(None), Document.purge_after <= now,
                                            Document.purged_at.is_(None))).all()
    for d in due:
        if d.storage_key:
            store.delete(d.storage_key)
        d.storage_key = None
        d.original_filename_enc = None
        d.purged_at = now
        audit(db, action="document.purged", target_type="document", target_id=d.id, reason_code="RETENTION_POLICY")
    db.commit()
    return len(due)
