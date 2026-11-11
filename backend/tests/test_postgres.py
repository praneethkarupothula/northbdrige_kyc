"""Acceptance 27: PostgreSQL migrations, concurrency, SKIP LOCKED claims, duplicate-identity races,
concurrent approvals. Requires TEST_POSTGRES_URL (e.g. postgresql://localhost:54329/nbkyc_test)."""

import threading

import pytest
from sqlalchemy import func, inspect, select

from app import synthetic as sy
from tests.helpers import checks_by_type, current, full_flow, supply_pan, start, submit, upload_doc

pytestmark = pytest.mark.postgres


def test_migrations_create_schema(env):
    from app.cli import migration_status
    from app.core.db import get_engine

    cur, head = migration_status()
    assert cur == head == "0002"
    tables = set(inspect(get_engine()).get_table_names())
    for t in ("users", "organizations", "staff_memberships", "sessions", "applications", "application_versions",
              "documents", "extraction_results", "verification_checks", "identity_claims", "confirmed_identities",
              "processing_jobs", "processing_events", "reviews", "notifications", "audit_events"):
        assert t in tables
    assert get_engine().dialect.name == "postgresql"


def test_full_journey_on_postgres(applicant, staff, worker):
    api = applicant()
    aid, cur = full_flow(api, worker, sy.person(0), pan=True)
    assert cur["check_status"] == "MATCH"
    rev = staff()
    d = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    assert rev.post(f"/v1/staff/applications/{aid}/approve",
                    json={"version_id": d["id"], "row_version": d["row_version"]}).status_code == 200
    assert current(api, aid)["review_status"] == "APPROVED"


def test_skip_locked_claims_each_job_once(env, applicant):
    from app.core.db import get_sessionmaker
    from app.models import ProcessingJob
    from app.workers import queue

    with get_sessionmaker()() as db:
        for i in range(12):
            queue.enqueue(db, "noop", {}, f"noop:{i}")
        db.commit()
    claimed, lock = [], threading.Lock()

    def run(n):
        with get_sessionmaker()() as db:
            while True:
                j = queue.claim(db, f"w{n}")
                if j is None:
                    return
                with lock:
                    claimed.append(j.id)
                j.status = "SUCCEEDED"
                db.commit()

    ts = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(claimed) == 12 and len(set(claimed)) == 12
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.max(ProcessingJob.attempts))) == 1


def test_concurrent_duplicate_identity_claims(env, applicant):
    """Two accounts submitting the same Aadhaar at the same moment both end in CONFLICT."""
    from app.workers.runner import Worker

    p = sy.person(0)
    apis = [applicant(), applicant()]
    for api in apis:
        aid = start(api)["id"]
        upload_doc(api, aid, sy.aadhaar_doc(p))
        supply_pan(api, aid)
        upload_doc(api, aid, sy.salary_doc(p))
        submit(api, aid)
        api.aid = aid
    barrier = threading.Barrier(2)

    def go(n):
        barrier.wait()
        Worker(worker_id=f"pw{n}").run_once()

    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    codes = [checks_by_type(current(a, a.aid))["DUPLICATE_IDENTITY_AADHAAR"][0]["code"] for a in apis]
    assert sorted(codes) == ["POTENTIAL_DUPLICATE_IDENTITY"] * 2 or sorted(codes) == sorted(
        ["NO_CONFLICTING_CLAIM", "POTENTIAL_DUPLICATE_IDENTITY"]), codes
    from app.core.db import get_sessionmaker
    from app.models import IdentityClaim
    with get_sessionmaker()() as db:
        statuses = sorted(db.scalars(select(IdentityClaim.status)).all())
    assert statuses == ["CONFLICT", "CONFLICT"], "the advisory lock serialises claims so both are flagged"


def test_concurrent_double_approval(applicant, staff, worker):
    api = applicant()
    aid, _ = full_flow(api, worker, sy.person(0))
    r1, r2 = staff("admin"), staff("admin")
    d = r1.get(f"/v1/staff/applications/{aid}").json()["current"]
    results = []
    barrier = threading.Barrier(2)

    def go(client):
        barrier.wait()
        results.append(client.post(f"/v1/staff/applications/{aid}/approve",
                                   json={"version_id": d["id"], "row_version": d["row_version"]}).status_code)

    ts = [threading.Thread(target=go, args=(c,)) for c in (r1, r2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 409]
    from app.core.db import get_sessionmaker
    from app.models import ConfirmedIdentity, Review
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(Review).where(Review.decision == "APPROVED")) == 1
        assert db.scalar(select(func.count()).select_from(ConfirmedIdentity)) == 1


def test_concurrent_claim_assignment(applicant, staff, worker):
    aid, _ = full_flow(applicant(), worker, sy.person(0))
    rs = [staff() for _ in range(4)]
    out = []
    barrier = threading.Barrier(4)

    def go(c):
        barrier.wait()
        out.append(c.post(f"/v1/staff/applications/{aid}/claim").status_code)

    ts = [threading.Thread(target=go, args=(c,)) for c in rs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200, 409, 409, 409]
