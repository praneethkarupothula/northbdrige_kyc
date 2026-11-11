"""Durable database-backed job queue.

* Jobs survive restarts (they live in ``processing_jobs``).
* Payloads hold opaque IDs only - never document bytes or PII.
* Claiming uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL so many workers can run safely.
  SQLite (development only) must run exactly one worker.
* A claimed job holds a lease that the worker renews with heartbeats; a job whose lease
  expires (worker crashed) is reclaimed by another worker.
* Bounded retries with exponential backoff; idempotency keys prevent duplicate enqueue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import is_postgres
from app.models import JobStatus, ProcessingJob


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(db: Session, kind: str, payload: dict, idempotency_key: str, *, run_after: datetime | None = None) -> ProcessingJob:
    existing = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    job = ProcessingJob(kind=kind, payload=payload, idempotency_key=idempotency_key,
                        max_attempts=get_settings().job_max_attempts, run_after=run_after or now())
    try:
        with db.begin_nested():
            db.add(job)
    except IntegrityError:
        return db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
    return job


def claim(db: Session, worker_id: str, kinds: list[str] | None = None) -> ProcessingJob | None:
    t = now()
    cond = or_(
        and_(ProcessingJob.status == JobStatus.QUEUED, ProcessingJob.run_after <= t),
        and_(ProcessingJob.status == JobStatus.RUNNING, ProcessingJob.lease_expires_at < t),
    )
    q = select(ProcessingJob).where(cond)
    if kinds:
        q = q.where(ProcessingJob.kind.in_(kinds))
    q = q.order_by(ProcessingJob.run_after).limit(1)
    if is_postgres(db):
        q = q.with_for_update(skip_locked=True)
    job = db.scalar(q)
    if job is None:
        db.rollback()
        return None
    lease = t + timedelta(seconds=get_settings().worker_lease_seconds)
    # conditional update guards SQLite (no row locks) against a concurrent claimer
    res = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job.id, ProcessingJob.status == job.status,
               ProcessingJob.attempts == job.attempts)
        .values(status=JobStatus.RUNNING, lease_owner=worker_id, lease_expires_at=lease, heartbeat_at=t,
                attempts=ProcessingJob.attempts + 1, updated_at=t)
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.refresh(job)
    return job


def heartbeat(db: Session, job_id: str, worker_id: str) -> bool:
    """Extend the lease. Returns False if this worker lost the lease or cancellation was requested."""
    t = now()
    res = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.lease_owner == worker_id,
               ProcessingJob.status == JobStatus.RUNNING)
        .values(heartbeat_at=t, lease_expires_at=t + timedelta(seconds=get_settings().worker_lease_seconds))
    )
    db.commit()
    if res.rowcount != 1:
        return False
    return not db.scalar(select(ProcessingJob.cancel_requested).where(ProcessingJob.id == job_id))


def is_cancel_requested(db: Session, job_id: str) -> bool:
    return bool(db.scalar(select(ProcessingJob.cancel_requested).where(ProcessingJob.id == job_id)))


def complete(db: Session, job: ProcessingJob, status: str = JobStatus.SUCCEEDED) -> None:
    job.status, job.finished_at, job.lease_owner, job.lease_expires_at = status, now(), None, None
    db.commit()


def fail(db: Session, job: ProcessingJob, error_class: str, *, retryable: bool) -> bool:
    """Record a failure. Returns True if the job was re-queued for another attempt."""
    job.last_error_class = error_class[:80]
    job.lease_owner, job.lease_expires_at = None, None
    if retryable and job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.run_after = now() + timedelta(seconds=min(300, 5 * 2 ** (job.attempts - 1)))
        db.commit()
        return True
    job.status, job.finished_at = JobStatus.FAILED, now()
    db.commit()
    return False
