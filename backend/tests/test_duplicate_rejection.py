from sqlalchemy import select

from app import synthetic as sy
from app.core.db import session_scope
from app.models import AuditEvent
from tests.helpers import current, full_flow, submit


def test_later_synthetic_reuse_is_rejected_even_if_values_match(applicant, staff, worker):
    admin = staff("admin")
    a, b = applicant("First Owner"), applicant("Second Account")
    p = sy.person(0)
    p.aadhaar, p.pan = "1211 4001 2003", "ABCDE1234F"
    aid_a, first = full_flow(a, worker, p)
    assert admin.post(f"/v1/staff/applications/{aid_a}/approve", json={"version_id": first["id"], "row_version": first["row_version"]}).status_code == 200
    aid_b, duplicate = full_flow(b, worker, p)
    assert duplicate["check_status"] == "MATCH"
    assert duplicate["review_status"] == "REJECTED"
    assert duplicate["decision_source"] == "automatic"
    assert "already been used for another approved account" in duplicate["applicant_message"]
    assert "contact support" in duplicate["applicant_message"]
    assert current(a, aid_a)["review_status"] == "APPROVED"
    assert any(n["kind"] == "REVIEW_REJECTED" for n in b.get("/v1/notifications").json()["items"])
    assert any(n["kind"] == "DUPLICATE_AUTO_REJECTED" for n in admin.get("/v1/notifications").json()["items"])
    detail = admin.get(f"/v1/staff/applications/{aid_b}").json()
    assert any(c["account_name"] == "First Owner" for identity in detail["identity"] for c in identity["duplicate_accounts"])
    assert "First Owner" not in str(current(b, aid_b))
    with session_scope() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.target_id == duplicate["id"],
                         AuditEvent.action == "application.automatically_rejected_duplicate"))


def test_same_account_resubmission_is_allowed(applicant, worker):
    a = applicant()
    aid, first = full_flow(a, worker, sy.person(0))
    assert a.post(f"/v1/applications/{aid}/revise", json={"row_version": first["row_version"]}).status_code == 200
    submit(a, aid)
    from app.workers.runner import drain
    drain(worker)
    assert current(a, aid)["review_status"] == "PENDING_REVIEW"


def test_identical_file_from_different_account_is_rejected(applicant, staff, worker):
    from tests.helpers import start, upload, upload_doc
    from app.workers.runner import drain
    shared = sy.RENDERERS["pdf"](sy.salary_doc(sy.person(0)))
    admin = staff("admin")
    for index in range(2):
        api = applicant()
        aid = start(api)["id"]
        upload_doc(api, aid, sy.aadhaar_doc(sy.person(index)))
        upload_doc(api, aid, sy.pan_doc(sy.person(index)))
        assert upload(api, aid, sy.salary_doc(sy.person(0)).category, shared).status_code == 201
        submit(api, aid)
        drain(worker)
        if index == 0:
            v = current(api, aid)
            assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": v["id"], "row_version": v["row_version"]}).status_code == 200
    assert admin.get(f"/v1/staff/applications/{aid}").json()["duplicate_documents"]
    second = current(api, aid)
    assert second["review_status"] == "REJECTED"
    assert second["decision_source"] == "automatic"


def test_existing_approved_account_is_not_revoked(applicant, staff, worker):
    a = applicant()
    aid, original = full_flow(a, worker, sy.person(0))
    admin = staff("admin")
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": original["id"], "row_version": original["row_version"],
    }).status_code == 200
    _, duplicate = full_flow(applicant(), worker, sy.person(0))
    assert duplicate["review_status"] == "REJECTED"
    assert current(a, aid)["review_status"] == "APPROVED"


def test_pending_or_rejected_prior_account_does_not_block(applicant, staff, worker):
    admin = staff("admin")
    a = applicant()
    aid, first = full_flow(a, worker, sy.person(0))
    _, second = full_flow(applicant(), worker, sy.person(0))
    assert second["review_status"] == "PENDING_REVIEW"
    assert admin.post(f"/v1/staff/applications/{aid}/reject", json={"version_id": first["id"],
        "row_version": first["row_version"], "reason_code": "OTHER", "applicant_message": "Rejected for this test submission."}).status_code == 200
    b = applicant()
    bid, third = full_flow(b, worker, sy.person(0))
    assert third["review_status"] == "PENDING_REVIEW"
    assert not any(n["kind"] in ("IDENTITY_CONFLICT", "REVIEW_REJECTED") for n in b.get("/v1/notifications").json()["items"])
    assert admin.post(f"/v1/staff/applications/{bid}/approve", json={"version_id": third["id"], "row_version": third["row_version"]}).status_code == 200


def test_cancelling_approval_removes_duplicate_block(applicant, staff, worker):
    admin, a = staff("admin"), applicant()
    aid, first = full_flow(a, worker, sy.person(0))
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": first["id"], "row_version": first["row_version"]}).status_code == 200
    v = current(a, aid)
    assert admin.post(f"/v1/staff/applications/{aid}/require-reverification", json={"version_id": v["id"], "row_version": v["row_version"], "applicant_message": "Fresh verification is now required."}).status_code == 200
    _, later = full_flow(applicant(), worker, sy.person(0))
    assert later["review_status"] == "PENDING_REVIEW"


def test_pending_submission_is_checked_again_when_admin_approves(applicant, staff, worker):
    admin, a, b = staff("admin"), applicant(), applicant()
    aid, first = full_flow(a, worker, sy.person(0))
    bid, second = full_flow(b, worker, sy.person(0))
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": first["id"], "row_version": first["row_version"]}).status_code == 200
    assert admin.post(f"/v1/staff/applications/{bid}/approve", json={"version_id": second["id"], "row_version": second["row_version"]}).status_code == 409


def test_closed_account_does_not_trigger_duplicate_rejection(applicant, staff, worker):
    admin, a = staff("admin"), applicant()
    aid, first = full_flow(a, worker, sy.person(0))
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": first["id"], "row_version": first["row_version"]}).status_code == 200
    v = current(a, aid)
    assert admin.post(f"/v1/staff/applications/{aid}/close-account", json={"version_id": v["id"], "row_version": v["row_version"], "applicant_message": "Account is closed for this test."}).status_code == 200
    bid, later = full_flow(applicant(), worker, sy.person(0))
    assert later["review_status"] == "PENDING_REVIEW"
    assert admin.post(f"/v1/staff/applications/{bid}/approve", json={"version_id": later["id"], "row_version": later["row_version"]}).status_code == 200
