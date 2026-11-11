"""Acceptance 2-3 & 26: object-level ownership, cross-organisation denial, evidence authorisation."""

from app import synthetic as sy
from tests.helpers import full_flow, start, upload_doc


def test_cross_applicant_denied(applicant):
    a, b = applicant(), applicant("Rohan Iyer")
    aid = start(a)["id"]
    doc = upload_doc(a, aid, sy.aadhaar_doc(sy.person(0)))["document_id"]
    for url in (f"/v1/applications/{aid}", f"/v1/applications/{aid}/events",
                f"/v1/documents/{doc}/pages/1", f"/v1/documents/{doc}/original"):
        assert b.get(url).status_code == 404, url
    assert b.post(f"/v1/documents/{doc}/reveal").status_code == 404
    assert b.delete(f"/v1/applications/{aid}/documents/{doc}").status_code == 404
    with b.c.stream("GET", f"/v1/applications/{aid}/events/stream") as r:
        assert r.status_code == 404
    assert a.get(f"/v1/applications/{aid}").status_code == 200


def test_cross_organisation_staff_denied(applicant, staff, worker):
    a = applicant()
    aid, _ = full_flow(a, worker, sy.person(0))
    other = staff("reviewer", org_slug="otherbank")
    assert other.get(f"/v1/staff/applications/{aid}").status_code == 404
    assert other.get("/v1/staff/applications").json()["total"] == 0
    nb = staff("reviewer")
    assert nb.get(f"/v1/staff/applications/{aid}").status_code == 200
    assert nb.get("/v1/staff/applications").json()["total"] == 1


def test_reveal_requires_assignment_and_is_audited(applicant, staff, worker):
    from sqlalchemy import select

    from app.core.db import session_scope
    from app.models import AuditEvent

    a = applicant()
    aid, cur = full_flow(a, worker, sy.person(0))
    doc = cur["documents"][0]["id"]
    rev = staff()
    assert rev.post(f"/v1/documents/{doc}/reveal").status_code == 403
    assert rev.get(f"/v1/documents/{doc}/pages/1", params={"reveal": "true"}).status_code == 403
    masked = rev.get(f"/v1/documents/{doc}/pages/1")
    assert masked.status_code == 200 and int(masked.headers["X-Redactions-Applied"]) >= 2
    assert masked.headers["cache-control"].startswith("no-store")
    assert rev.post(f"/v1/staff/applications/{aid}/claim").status_code == 200
    r = rev.post(f"/v1/documents/{doc}/reveal")
    assert r.status_code == 200 and r.json()["fields"]["full_name"] == "Asha Verma"
    detail = rev.get(f"/v1/staff/applications/{aid}").json()
    assert "Asha Verma" not in str(detail["current"]), "default staff view must be masked"
    with session_scope() as db:
        actions = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.application_id == aid))}
        details = [str(e.details) for e in db.scalars(select(AuditEvent))]
    assert "document.fields_revealed" in actions and "document.preview" in actions
    assert not any("Asha" in d for d in details), "audit records must not copy sensitive values"


def test_admin_can_review_full_applicant_and_documents_then_approve(applicant, staff, worker):
    a = applicant("Asha Verma")
    aid, _ = full_flow(a, worker, sy.person(0), pan=True, bank=True)
    admin = staff("admin")
    response = admin.get(f"/v1/staff/applications/{aid}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["applicant"]["full_name"] == "Asha Verma"
    assert detail["applicant"]["email"] == a.email
    assert detail["can_reveal"] is True
    assert detail["current"]["review_status"] == "PENDING_REVIEW"
    assert {d["category"] for d in detail["current"]["documents"]} == {
        "AADHAAR", "PAN", "SALARY_SLIP", "BANK_STATEMENT",
    }
    queue = admin.get("/v1/staff/applications").json()
    assert queue["items"][0]["applicant"] == "Asha Verma"
    for doc in detail["current"]["documents"]:
        assert admin.get(f"/v1/documents/{doc['id']}/original").status_code == 200
        fields = admin.post(f"/v1/documents/{doc['id']}/reveal")
        assert fields.status_code == 200
        assert fields.json()["fields"]["full_name"] == "Asha Verma"
    reviewer = staff("reviewer")
    masked = reviewer.get(f"/v1/staff/applications/{aid}").json()["applicant"]
    assert masked["full_name"] is None and masked["email"] is None
    assert staff("admin", org_slug="otherbank").get(f"/v1/staff/applications/{aid}").status_code == 404
    assert a.get(f"/v1/staff/applications/{aid}").status_code == 403
    cur = detail["current"]
    assert detail["approval_blockers"] == []
    assert admin.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
    }).status_code == 200
    assert a.get(f"/v1/applications/{aid}").json()["current"]["review_status"] == "APPROVED"


def test_applicant_can_preview_own_draft_before_submission(applicant):
    owner, other = applicant(), applicant("Other Applicant")
    aid = start(owner)["id"]
    doc = upload_doc(owner, aid, sy.aadhaar_doc(sy.person(0)))["document_id"]
    response = owner.get(f"/v1/documents/{doc}/pages/1", params={"reveal": "true"})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert owner.get(f"/v1/documents/{doc}/original").status_code == 200
    assert other.get(f"/v1/documents/{doc}/pages/1", params={"reveal": "true"}).status_code == 404
