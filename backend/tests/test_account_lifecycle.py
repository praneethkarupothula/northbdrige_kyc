from tests.helpers import full_flow, current, upload_doc, submit
from app import synthetic as sy
from app.workers.runner import drain


def approved(applicant, staff, worker):
    user, admin = applicant(), staff("admin")
    aid, version = full_flow(user, worker, sy.person(0))
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": version["id"], "row_version": version["row_version"]}).status_code == 200
    return user, admin, aid, current(user, aid)


def test_revoke_requires_fresh_upload_and_new_review(applicant, staff, worker):
    user, admin, aid, v = approved(applicant, staff, worker)
    body = {"version_id": v["id"], "row_version": v["row_version"], "applicant_message": "Please provide up to date documents."}
    assert staff("reviewer").post(f"/v1/staff/applications/{aid}/require-reverification", json=body).status_code == 403
    assert admin.post(f"/v1/staff/applications/{aid}/require-reverification", json=body).status_code == 200
    fresh = current(user, aid)
    assert fresh["state"] == "DRAFT"
    assert fresh["review_status"] != "APPROVED"
    assert fresh["documents"] == []
    assert fresh["version_number"] == v["version_number"] + 1
    assert admin.post(f"/v1/staff/applications/{aid}/require-reverification", json=body).status_code == 409
    assert any(n["kind"] == "KYC_REVERIFICATION_REQUIRED" for n in user.get("/v1/notifications").json()["items"])
    assert any(h["review_status"] == "APPROVED" for h in admin.get(f"/v1/staff/applications/{aid}").json()["history"])
    for doc in (sy.aadhaar_doc(sy.person(0)), sy.pan_doc(sy.person(0)), sy.salary_doc(sy.person(0))):
        upload_doc(user, aid, doc)
    submit(user, aid)
    drain(worker)
    fresh = current(user, aid)
    assert fresh["review_status"] == "PENDING_REVIEW"
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={"version_id": fresh["id"], "row_version": fresh["row_version"]}).status_code == 200


def test_close_account_revokes_access_retains_history(applicant, staff, worker):
    user, admin, aid, v = approved(applicant, staff, worker)
    body = {"version_id": v["id"], "row_version": v["row_version"], "applicant_message": "Account closed following administrative review."}
    assert staff("reviewer").post(f"/v1/staff/applications/{aid}/close-account", json=body).status_code == 403
    assert admin.post(f"/v1/staff/applications/{aid}/close-account", json=body).status_code == 200
    assert user.get(f"/v1/applications/{aid}").status_code == 401
    detail = admin.get(f"/v1/staff/applications/{aid}").json()
    assert detail["applicant"]["is_active"] is False
    assert detail["current"]["review_status"] == "REJECTED"
    assert detail["reviews"]
    assert any(e["action"] == "applicant.account_closed" for e in detail["audit"])
