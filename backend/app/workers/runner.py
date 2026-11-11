"""Worker process: ``python -m app.cli worker`` (works on Windows, macOS and Linux)."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models import JobStatus, ProcessingJob
from app.observability.telemetry import get_logger, safe_error_class
from app.pipeline.llm import ProviderError
from app.pipeline.orchestrator import JobCancelled, mark_failed, process_version
from app.services import retention
from app.workers import queue

log = get_logger()


class Worker:
    def __init__(self, worker_id: str | None = None, provider=None):
        self.worker_id = worker_id or f"{socket.gethostname()[:30]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.stop_event = threading.Event()
        self.provider = provider
        self.Session = get_sessionmaker()
        self._last_sweep = 0.0

    # ------------------------------------------------------------------ lifecycle
    def install_signal_handlers(self) -> None:
        def _stop(*_):
            log.info("worker_stopping", worker_id=self.worker_id)
            self.stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _stop)

    def run_forever(self) -> None:
        s = get_settings()
        log.info("worker_started", worker_id=self.worker_id, milestone="ready")
        while not self.stop_event.is_set():
            self.maybe_schedule_sweep()
            if not self.run_once():
                self.stop_event.wait(s.worker_poll_seconds)
        log.info("worker_stopped", worker_id=self.worker_id)

    def maybe_schedule_sweep(self) -> None:
        s = get_settings()
        if time.monotonic() - self._last_sweep < s.retention_sweep_minutes * 60:
            return
        self._last_sweep = time.monotonic()
        bucket = datetime.now(UTC).strftime("%Y%m%d%H") + str(datetime.now(UTC).minute // max(1, s.retention_sweep_minutes))
        with self.Session() as db:
            queue.enqueue(db, "retention_sweep", {}, f"retention_sweep:{bucket}")
            db.commit()

    # ------------------------------------------------------------------ one job
    def run_once(self) -> bool:
        with self.Session() as db:
            job = queue.claim(db, self.worker_id)
            if job is None:
                return False
            job_id = job.id
        log.info("job_claimed", job_id=job_id, job_kind=job.kind, attempt=job.attempts, worker_id=self.worker_id)
        stop_hb = threading.Event()
        cancelled = threading.Event()
        hb = threading.Thread(target=self._heartbeat, args=(job_id, stop_hb, cancelled), daemon=True)
        hb.start()
        try:
            self._execute(job_id, cancelled)
        finally:
            stop_hb.set()
            hb.join(timeout=5)
        return True

    def _heartbeat(self, job_id: str, stop: threading.Event, cancelled: threading.Event) -> None:
        interval = get_settings().worker_heartbeat_seconds
        while not stop.wait(interval):
            with self.Session() as db:
                if not queue.heartbeat(db, job_id, self.worker_id):
                    cancelled.set()

    def _execute(self, job_id: str, cancelled: threading.Event) -> None:
        t0 = time.monotonic()
        with self.Session() as db:
            job = db.get(ProcessingJob, job_id)

            def cancel_check() -> bool:
                if cancelled.is_set():
                    return True
                with self.Session() as db2:
                    return queue.is_cancel_requested(db2, job_id)

            try:
                if job.kind == "process_version":
                    result = process_version(db, job.payload["version_id"], int(job.payload["attempt"]),
                                             cancel_check=cancel_check, provider=self.provider)
                    log.info("job_result", job_id=job_id, status=result)
                elif job.kind == "retention_sweep":
                    n = retention.sweep(db)
                    log.info("retention_sweep_done", job_id=job_id, count=n)
                else:
                    raise ValueError("unknown job kind")
                job = db.get(ProcessingJob, job_id)
                queue.complete(db, job)
            except JobCancelled:
                job = db.get(ProcessingJob, job_id)
                queue.complete(db, job, JobStatus.CANCELLED)
                log.info("job_cancelled", job_id=job_id)
            except ProviderError as exc:
                db.rollback()
                job = db.get(ProcessingJob, job_id)
                will_retry = queue.fail(db, job, exc.code, retryable=exc.retryable)
                if job.kind == "process_version":
                    mark_failed(db, job.payload["version_id"], exc.code, will_retry=will_retry)
                log.warning("job_failed", job_id=job_id, error_class=exc.code, retry_count=job.attempts)
            except Exception as exc:  # noqa: BLE001 - never log the message (it may contain data)
                db.rollback()
                job = db.get(ProcessingJob, job_id)
                err = safe_error_class(exc)
                will_retry = queue.fail(db, job, err, retryable=True)
                if job.kind == "process_version":
                    mark_failed(db, job.payload["version_id"], "INTERNAL_ERROR", will_retry=will_retry)
                log.error("job_crashed", job_id=job_id, error_class=err, retry_count=job.attempts)
            finally:
                log.info("job_finished", job_id=job_id, duration_ms=int((time.monotonic() - t0) * 1000))


def drain(worker: Worker, max_jobs: int = 50) -> int:
    """Process jobs until the queue is empty (used by tests and the eval script)."""
    n = 0
    while n < max_jobs and worker.run_once():
        n += 1
    return n
