"""Acceptance 20-23: human approval, reject, changes requested, stale approval prevention."""

from app import synthetic as sy
from app.workers.runner import drain
from tests.helpers import current, full_flow, submit


def _approve(rev, aid, cur):
    return rev.post(f"/v1/staff/applications/{aid}/approve",
                    json={"version_id": cur["id"], "row_version": cur["row_version"]})


def test_match_requires_human_approval_and_applicant_sees_it(applicant, staff, worker):
    a = applicant()
    aid, cur = full_flow(a, worker, sy.person(0))
    assert cur["check_status"] == "MATCH" and cur["review_status"] == "PENDING_REVIEW"
    assert cur["status"]["key"] == "MATCH_AWAITING_REVIEW"
    rev = staff()
    dash = rev.get("/v1/staff/dashboard").json()
    assert dash["pending_review"] == 1
    assert rev.post(f"/v1/staff/applications/{aid}/claim").status_code == 200
    detail = rev.get(f"/v1/staff/applications/{aid}").json()
    assert detail["approval_blockers"] == []
    assert _approve(rev, aid, detail["current"]).status_code == 200
    cur = current(a, aid)
    assert cur["review_status"] == "APPROVED" and cur["check_status"] == "MATCH"
    assert cur["status"]["headline"] == "KYC completed and approved"
    notes = a.get("/v1/notifications").json()
    assert notes["unread"] >= 2 and notes["items"][0]["kind"] == "REVIEW_APPROVED"
    assert notes["items"][0]["title"] == "KYC completed and approved"
    assert "No further action" in notes["items"][0]["body"]
    events = a.get(f"/v1/applications/{aid}/events").json()["events"]
    assert events[-1]["stage"] == "DECISION"
    # double approval prevented
    assert _approve(rev, aid, detail["current"]).status_code == 409
    fresh = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    assert _approve(rev, aid, fresh).status_code == 409


def test_cannot_approve_mismatch(applicant, staff, worker):
    p = sy.person(0)
    _, cur = full_flow(applicant(), worker, p, slips=[sy.salary_doc(p, name_override="Someone Different")])
    aid = applicant  # noqa
    rev = staff()
    item = rev.get("/v1/staff/applications").json()["items"][0]
    detail = rev.get(f"/v1/staff/applications/{item['application_id']}").json()
    assert detail["current"]["check_status"] == "MISMATCH"
    assert detail["approval_blockers"]
    r = _approve(rev, item["application_id"], detail["current"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "APPROVAL_BLOCKED"


def test_reject_requires_reason(applicant, staff, worker):
    a = applicant()
    aid, _ = full_flow(a, worker, sy.person(0))
    rev = staff()
    cur = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    assert rev.post(f"/v1/staff/applications/{aid}/reject", json={
        "version_id": cur["id"], "row_version": cur["row_version"], "reason_code": "OTHER"}).status_code == 422
    r = rev.post(f"/v1/staff/applications/{aid}/reject", json={
        "version_id": cur["id"], "row_version": cur["row_version"], "reason_code": "DOCUMENTS_INCONSISTENT",
        "applicant_message": "The salary slip could not be confirmed against your identity document.",
        "internal_note": "Employer letterhead looks inconsistent."})
    assert r.status_code == 200
    ac = current(a, aid)
    assert ac["review_status"] == "REJECTED"
    assert "could not be confirmed" in ac["status"]["detail"]
    assert "letterhead" not in str(a.get(f"/v1/applications/{aid}").json()), "internal notes stay internal"


def test_changes_requested_then_resubmission(applicant, staff, worker):
    p = sy.person(0)
    a = applicant()
    aid, _ = full_flow(a, worker, p, slips=[sy.salary_doc(p, name_override="Asha Varma")])
    rev = staff()
    cur = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    r = rev.post(f"/v1/staff/applications/{aid}/request-changes", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
        "items": [{"category": "SALARY_SLIP", "instruction": "Upload a salary slip that shows your name as on Aadhaar."}],
        "applicant_message": "Please upload a corrected salary slip."})
    assert r.status_code == 200
    ac = current(a, aid)
    assert ac["review_status"] == "CHANGES_REQUESTED"
    assert ac["requested_items"][0]["category"] == "SALARY_SLIP"
    # applicant cannot edit submitted version in place
    from tests.helpers import upload
    assert upload(a, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p))).status_code == 409
    r = a.post(f"/v1/applications/{aid}/revise", json={"row_version": ac["row_version"]})
    assert r.status_code == 200
    v2 = r.json()["current"]
    assert v2["version_number"] == 2 and v2["state"] == "DRAFT"
    slip = next(d for d in v2["documents"] if d["category"] == "SALARY_SLIP")
    r = upload(a, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p)), replace=slip["id"])
    assert r.status_code == 201
    submit(a, aid)
    drain(worker)
    v2 = current(a, aid)
    assert v2["check_status"] == "MATCH" and v2["review_status"] == "PENDING_REVIEW"
    assert len(a.get(f"/v1/applications/{aid}").json()["history"]) == 2


def test_stale_approval_after_resubmission(applicant, staff, worker):
    p = sy.person(0)
    a = applicant()
    aid, _ = full_flow(a, worker, p)
    rev = staff()
    stale = rev.get(f"/v1/staff/applications/{aid}").json()["current"]  # reviewer loads v1
    ac = current(a, aid)
    assert a.post(f"/v1/applications/{aid}/revise", json={"row_version": ac["row_version"]}).status_code == 200
    r = _approve(rev, aid, stale)
    assert r.status_code == 409 and "newer version" in r.json()["detail"]
    submit(a, aid)
    drain(worker)
    fresh = rev.get(f"/v1/staff/applications/{aid}").json()["current"]
    assert fresh["version_number"] == 2
    assert _approve(rev, aid, {**fresh, "row_version": fresh["row_version"] - 1}).status_code == 409
    assert _approve(rev, aid, fresh).status_code == 200


def test_claim_release_and_notes(applicant, staff, worker):
    aid, _ = full_flow(applicant(), worker, sy.person(0))
    r1, r2 = staff(), staff()
    assert r1.post(f"/v1/staff/applications/{aid}/claim").status_code == 200
    assert r2.post(f"/v1/staff/applications/{aid}/claim").status_code == 409
    cur = r2.get(f"/v1/staff/applications/{aid}").json()["current"]
    assert _approve(r2, aid, cur).status_code == 409, "assigned to another reviewer"
    assert r2.post(f"/v1/staff/applications/{aid}/notes", json={"version_id": cur["id"], "note": "Looks fine"}).status_code == 200
    assert r1.post(f"/v1/staff/applications/{aid}/release").status_code == 200
    assert r2.post(f"/v1/staff/applications/{aid}/claim").status_code == 200
    q = r2.get("/v1/staff/applications", params={"assignment": "me"}).json()
    assert q["total"] == 1 and q["items"][0]["assigned_to_me"]
    assert r2.get("/v1/staff/applications", params={"review_status": "APPROVED"}).json()["total"] == 0
    assert r2.get("/v1/staff/applications", params={"document_type": "PAN"}).json()["total"] == 1
    exp = r2.get(f"/v1/staff/applications/{aid}/export.csv")
    assert exp.status_code == 200 and "Asha" not in exp.text and "NAME_SALARY_VS_AADHAAR" in exp.text


def test_reject_resubmitted_application_with_existing_retention_schedule(applicant, staff, worker):
    from app.workers.runner import drain

    a = applicant()
    aid, cur = full_flow(a, worker, sy.person(0))
    reviewer = staff()

    def reject(version):
        return reviewer.post(f"/v1/staff/applications/{aid}/reject", json={
            "version_id": version["id"], "row_version": version["row_version"],
            "reason_code": "OTHER", "applicant_message": "Please provide a corrected document.",
        })

    assert reject(cur).status_code == 200
    assert a.post(f"/v1/applications/{aid}/revise", json={"row_version": current(a, aid)["row_version"]}).status_code == 200
    submit(a, aid)
    drain(worker)
    updated = current(a, aid)
    assert reject(updated).status_code == 200
    assert current(a, aid)["review_status"] == "REJECTED"
