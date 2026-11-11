"""Persisted progress events. Messages are fixed, human-readable strings without PII."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ApplicationVersion, ProcessingEvent


def emit(
    db: Session,
    version: ApplicationVersion,
    stage: str,
    status: str,
    message: str,
    *,
    document_id: str | None = None,
    commit: bool = True,
) -> ProcessingEvent:
    ev = ProcessingEvent(
        application_id=version.application_id,
        version_id=version.id,
        document_id=document_id,
        attempt=version.processing_attempt,
        stage=str(stage),
        status=status,
        message=message[:300],
    )
    db.add(ev)
    if commit:
        db.commit()
    return ev
