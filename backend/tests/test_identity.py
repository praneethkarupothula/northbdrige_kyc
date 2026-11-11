"""Acceptance 13-14: duplicate identity claims, legitimate resubmission, conflict resolution."""

from app import synthetic as sy
from tests.helpers import checks_by_type, current, full_flow


def test_identity_claimed_by_other_account(applicant, staff, worker):
    p = sy.person(0)
    a, b = applicant(), applicant()
    aid_a, _ = full_flow(a, worker, p)
    aid_b, duplicate = full_flow(b, worker, p)
    assert duplicate["review_status"] == "PENDING_REVIEW"
    assert duplicate["check_status"] == "MATCH"
    assert a.email not in str(current(b, aid_b))
    reviewer = staff()
    for aid in (aid_a, aid_b):
        assert reviewer.get(f"/v1/staff/applications/{aid}").json()["approval_blockers"] == []
    assert not any(n["kind"] == "IDENTITY_CONFLICT" for n in b.get("/v1/notifications").json()["items"])


def test_legitimate_resubmission_same_account(applicant, staff, worker):
    p = sy.person(0)
    a = applicant()
    aid, cur = full_flow(a, worker, p)
    from app.workers.runner import drain
    from tests.helpers import submit
    assert a.post(f"/v1/applications/{aid}/revise", json={"row_version": cur["row_version"]}).status_code == 200
    submit(a, aid)
    drain(worker)
    cur = current(a, aid)
    assert checks_by_type(cur)["DUPLICATE_IDENTITY_AADHAAR"][0]["code"] == "NO_CONFLICTING_CLAIM"
    assert cur["check_status"] == "MATCH"
    rev = staff()
    d = rev.get(f"/v1/staff/applications/{aid}").json()
    assert rev.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": d["current"]["id"], "row_version": d["current"]["row_version"]}).status_code == 200


def test_confirmed_identity_blocks_other_account_but_not_holder(applicant, staff, worker):
    p = sy.person(0)
    a = applicant()
    aid, _ = full_flow(a, worker, p)
    rev = staff()
    d = rev.get(f"/v1/staff/applications/{aid}").json()
    rev.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": d["current"]["id"],
                                                           "row_version": d["current"]["row_version"]})
    b = applicant()
    _, cb = full_flow(b, worker, p)
    assert checks_by_type(cb)["DUPLICATE_IDENTITY_AADHAAR"][0]["code"] == "IDENTITY_HELD_BY_ANOTHER_ACCOUNT"


def test_uncertain_identifier_not_reserved(env):
    from app.core.db import session_scope
    from app.models import Application, ApplicationVersion, User
    from app.services.identity import IdentityInput, register_claim
    from app.services.policy import default_org

    with session_scope() as db:
        org = default_org(db)
        users = [User(email=f"u{i}@example.com", full_name="X Y", role="applicant") for i in range(2)]
        db.add_all(users)
        db.flush()
        vers = []
        for i, u in enumerate(users):
            app = Application(applicant_id=u.id, organization_id=org.id, reference=f"NB-T{i}")
            db.add(app)
            db.flush()
            v = ApplicationVersion(application_id=app.id, version_number=1)
            db.add(v)
            db.flush()
            vers.append((u, app, v))
        u, app, v = vers[0]
        out = register_claim(db, user_id=u.id, application_id=app.id, version_id=v.id, item=IdentityInput(
            "AADHAAR", "234567890124", True, 41.0, "XXXX XXXX 0124", "X Y", None))
        assert out.claim_status == "UNTRUSTED" and out.status == "INCOMPLETE"
        u2, app2, v2 = vers[1]
        out2 = register_claim(db, user_id=u2.id, application_id=app2.id, version_id=v2.id, item=IdentityInput(
            "AADHAAR", "234567890124", True, None, "XXXX XXXX 0124", "X Y", None))
        assert out2.code == "NO_CONFLICTING_CLAIM", "an untrusted OCR read must not lock out the real holder"


def test_conflict_notifies_both_applicants_and_scoped_admin_without_spam(applicant, staff, worker):
    admin = staff("admin")
    other_admin = staff("admin", org_slug="otherbank")
    a, b = applicant(), applicant()
    aid_a, first = full_flow(a, worker, sy.person(0))
    assert admin.post(f"/v1/staff/applications/{aid_a}/approve", json={"version_id": first["id"], "row_version": first["row_version"]}).status_code == 200
    aid_b, _ = full_flow(b, worker, sy.person(0))
    for owner, aid, peer in [(a, aid_a, b), (b, aid_b, a)]:
        notes = [n for n in owner.get("/v1/notifications").json()["items"] if n["kind"] == "IDENTITY_CONFLICT"]
        assert len(notes) == 1
        assert notes[0]["application_id"] == aid
        assert "sign in or contact support" in notes[0]["body"]
        assert "forged or altered" in notes[0]["body"]
        assert peer.email not in str(notes)
        assert current(owner, aid)["review_status"] == ("APPROVED" if aid == aid_a else "REJECTED")
    notices = [n for n in admin.get("/v1/notifications").json()["items"] if n["kind"] == "IDENTITY_CONFLICT"]
    assert {n["application_id"] for n in notices} == {aid_a, aid_b}
    assert other_admin.get("/v1/notifications").json()["items"] == []
    assert admin.post(f"/v1/staff/applications/{aid_b}/reprocess").status_code == 409
    from app.pipeline.orchestrator import process_version
    from app.core.db import session_scope
    with session_scope() as db:
        cur = current(b, aid_b)
        assert process_version(db, cur["id"], cur["processing_attempt"]) == "SKIPPED_FINAL_DECISION"
    assert len([n for n in b.get("/v1/notifications").json()["items"] if n["kind"] == "IDENTITY_CONFLICT"]) == 1
