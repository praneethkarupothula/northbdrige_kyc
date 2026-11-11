"""Acceptance 24-26: SSE + polling, worker restart/retry/idempotency, retention and deletion."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app import synthetic as sy
from app.workers import queue
from app.workers.runner import Worker, drain
from tests.helpers import current, full_flow, supply_pan, start, submit, upload_doc


def _prepared(api):
    aid = start(api)["id"]
    upload_doc(api, aid, sy.aadhaar_doc(sy.person(0)))
    supply_pan(api, aid)
    upload_doc(api, aid, sy.salary_doc(sy.person(0)))
    submit(api, aid)
    return aid


def _read_sse(api, url, headers=None, max_events=500):
    events, cur = [], {}
    with api.c.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("id:"):
                cur["id"] = int(line[3:].strip())
            elif line.startswith("event:"):
                cur["event"] = line[6:].strip()
            elif line.startswith("data:"):
                cur["data"] = line[5:].strip()
            elif line == "" and cur:
                events.append(cur)
                cur = {}
                if len(events) >= max_events:
                    break
    return events


def test_sse_stream_and_reconnect(applicant, worker, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SSE_MAX_SECONDS", "1")
    get_settings.cache_clear()
    api = applicant()
    aid = _prepared(api)
    drain(worker)
    evs = _read_sse(api, f"/v1/applications/{aid}/events/stream")
    progress = [e for e in evs if e.get("event") == "progress"]
    stages = [__import__("json").loads(e["data"])["stage"] for e in progress]
    for s in ("UPLOAD_RECEIVED", "FILE_VALIDATION", "QUALITY_INSPECTION", "TEXT_EXTRACTION", "PII_MASKING",
              "LLM_EXTRACTION", "SCHEMA_VALIDATION", "DUPLICATE_CHECK", "NAME_CHECKS", "AWAITING_REVIEW"):
        assert s in stages, s
    assert any(e.get("event") == "status" for e in evs)
    ids = [e["id"] for e in progress]
    assert ids == sorted(ids)
    mid = ids[len(ids) // 2]
    again = [e for e in _read_sse(api, f"/v1/applications/{aid}/events/stream", {"Last-Event-ID": str(mid)})
             if e.get("event") == "progress"]
    assert again and all(e["id"] > mid for e in again)
    assert len(again) == len([i for i in ids if i > mid])
    data = __import__("json").loads(progress[0]["data"])
    assert {"id", "version_id", "attempt", "stage", "status", "message"} <= set(data)


def test_polling_fallback(applicant, worker):
    api = applicant()
    aid = _prepared(api)
    first = api.get(f"/v1/applications/{aid}/events").json()
    assert first["status"]["processing_status"] == "QUEUED"
    last = first["events"][-1]["id"]
    drain(worker)
    nxt = api.get(f"/v1/applications/{aid}/events", params={"after": last}).json()
    assert nxt["events"] and all(e["id"] > last for e in nxt["events"])
    assert nxt["status"]["review_status"] == "PENDING_REVIEW"


def test_worker_crash_lease_recovery(applicant, env):
    from app.core.db import session_scope
    from app.models import ProcessingJob

    api = applicant()
    aid = _prepared(api)
    with session_scope() as db:
        crashed = queue.claim(db, "worker-that-dies")  # claims and then "crashes"
        assert crashed is not None
    b = Worker(worker_id="worker-b")
    assert not b.run_once(), "lease still held"
    with session_scope() as db:
        db.execute(update(ProcessingJob).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert b.run_once()
    with session_scope() as db:
        job = db.scalar(select(ProcessingJob))
        assert job.status == "SUCCEEDED" and job.attempts == 2
    assert current(api, aid)["check_status"] == "MATCH"


def test_retry_then_success_and_idempotent_rerun(applicant, env, monkeypatch):
    from app.core.db import session_scope
    from app.models import ExtractionResult, ProcessingJob, VerificationCheck
    from app.pipeline.llm import ProviderTransportError, ScriptedProvider
    from app.pipeline.orchestrator import process_version

    monkeypatch.setattr("app.pipeline.orchestrator.time.sleep", lambda s: None)

    class Flaky:
        name = "flaky"

        def __init__(self):
            self.n = 0

        def extract(self, payload, hint):
            self.n += 1
            if self.n <= 2:  # first job attempt fails (transport retry inside also fails)
                raise ProviderTransportError("503")
            return ScriptedProvider().extract(payload, hint)

    api = applicant()
    aid = _prepared(api)
    w = Worker(provider=Flaky())
    assert w.run_once()
    assert current(api, aid)["processing_status"] == "QUEUED"
    with session_scope() as db:
        db.execute(update(ProcessingJob).values(run_after=datetime.now(UTC)))
    assert w.run_once()
    cur = current(api, aid)
    assert cur["processing_status"] == "SUCCEEDED" and cur["check_status"] == "MATCH"
    # idempotency: duplicate enqueue is a no-op; re-running the same attempt does not duplicate rows
    with session_scope() as db:
        before = (db.scalar(select(func.count()).select_from(ExtractionResult)),
                  db.scalar(select(func.count()).select_from(VerificationCheck)))
        job = db.scalar(select(ProcessingJob))
        assert queue.enqueue(db, "process_version", job.payload, job.idempotency_key).id == job.id
        db.commit()
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 1
    with session_scope() as db:
        from app.models import ApplicationVersion
        v = db.get(ApplicationVersion, job.payload["version_id"])
        v.processing_status = "RUNNING"
        db.commit()
        process_version(db, job.payload["version_id"], job.payload["attempt"])
    with session_scope() as db:
        after = (db.scalar(select(func.count()).select_from(ExtractionResult)),
                 db.scalar(select(func.count()).select_from(VerificationCheck)))
    assert before == after


def test_superseded_attempt_is_skipped(applicant, env):
    from app.core.db import session_scope
    from app.pipeline.orchestrator import process_version

    api = applicant()
    aid = _prepared(api)
    vid = current(api, aid)["id"]
    with session_scope() as db:
        assert process_version(db, vid, 99) == "SKIPPED_SUPERSEDED_ATTEMPT"


def test_cancel_queued_processing(applicant, env):
    api = applicant()
    aid = _prepared(api)
    cur = current(api, aid)
    r = api.post(f"/v1/applications/{aid}/cancel", json={"row_version": cur["row_version"]})
    assert r.status_code == 200 and r.json()["current"]["state"] == "DRAFT"
    assert not Worker().run_once()


def test_retention_purges_originals_after_decision(applicant, staff, worker):
    from app.core.db import session_scope
    from app.models import AuditEvent, Document
    from app.services import retention
    from app.storage.blob import get_store

    api = applicant()
    aid, cur = full_flow(api, worker, sy.person(0))
    rev = staff()
    d = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    rev.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": d["id"], "row_version": d["row_version"]})
    doc_id = cur["documents"][0]["id"]
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        key = doc.storage_key
        assert doc.purge_after is not None
        assert retention.sweep(db) == 0, "not yet due"
        n = retention.sweep(db, now=datetime.now(UTC) + timedelta(days=8))
        assert n == len(cur["documents"])
    assert not get_store().exists(key)
    assert api.get(f"/v1/documents/{doc_id}/pages/1").status_code == 410
    after = current(api, aid)
    assert after["review_status"] == "APPROVED" and after["documents"][0]["purged"] is True
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "document.purged")) == len(cur["documents"])


def test_encrypted_at_rest(applicant, env):
    from app.core.config import get_settings

    api = applicant()
    aid = start(api)["id"]
    p = sy.person(0)
    upload_doc(api, aid, sy.aadhaar_doc(p))
    files = list(get_settings().storage_dir.rglob("*.bin"))
    assert files and all(not f.read_bytes().startswith(b"%PDF") for f in files)
    assert all(p.name.encode() not in f.read_bytes() for f in files)
