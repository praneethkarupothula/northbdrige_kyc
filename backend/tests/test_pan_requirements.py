"""Mandatory PAN and salary-slip name/PAN evidence for the current matching rules."""
from app import synthetic as sy
from tests.helpers import full_flow, start, upload_doc


def test_pan_upload_cannot_be_skipped(applicant, staff):
    a = applicant()
    app = start(a)
    aid = app["id"]
    assert app["policy"]["pan_policy"] == "required"
    assert a.patch(f"/v1/applications/{aid}/draft", json={"pan_choice": "UNAVAILABLE"}).status_code == 422
    upload_doc(a, aid, sy.aadhaar_doc(sy.person(0)))
    upload_doc(a, aid, sy.salary_doc(sy.person(0)))
    cur = a.get(f"/v1/applications/{aid}").json()["current"]
    response = a.post(f"/v1/applications/{aid}/submit", json={"consent": True, "row_version": cur["row_version"]})
    assert response.status_code == 422
    assert "PAN" in response.text
    admin = staff("admin")
    policy = admin.get("/v1/admin/policy").json()
    assert admin.put("/v1/admin/policy", json={**{k: v for k, v in policy.items() if k != "notice"},
                                             "pan_policy": "optional"}).status_code == 422


def test_matching_names_and_salary_pan_can_be_approved(applicant, staff, worker):
    aid, cur = full_flow(applicant(), worker, sy.person(0))
    assert cur["check_status"] == "MATCH"
    for kind in ("NAME_PAN_VS_AADHAAR", "NAME_SALARY_VS_AADHAAR", "NAME_SALARY_VS_PAN", "PAN_NUMBER_SALARY_VS_PAN"):
        assert all(c["status"] == "MATCH" for c in cur["checks"] if c["check_type"] == kind)
        assert any(c["check_type"] == kind for c in cur["checks"])
    reviewer = staff()
    assert reviewer.get(f"/v1/staff/applications/{aid}").json()["approval_blockers"] == []


def test_salary_pan_mismatch_blocks_match_and_approval(applicant, staff, worker):
    p = sy.person(0)
    slip = sy.salary_doc(p)
    slip.lines = [f"PAN: {sy.person(1).pan}" if line.startswith("PAN:") else line for line in slip.lines]
    aid, cur = full_flow(applicant(), worker, p, slips=[slip])
    assert cur["check_status"] == "MISMATCH"
    assert any(c["code"] == "SALARY_PAN_DIFFERS" for c in cur["checks"])
    reviewer = staff()
    assert reviewer.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
    }).status_code == 409


def test_each_salary_slip_must_print_pan(applicant, worker):
    p = sy.person(0)
    missing = sy.salary_doc(p, sy.SalaryInfo(month="February"))
    missing.lines = [line for line in missing.lines if not line.startswith("PAN:")]
    _, cur = full_flow(applicant(), worker, p, slips=[sy.salary_doc(p), missing])
    assert cur["check_status"] == "INCOMPLETE"
    assert any(c["code"] == "SALARY_PAN_NOT_EXTRACTED" for c in cur["checks"])
    assert any(c["code"] == "SALARY_PAN_AGREES" for c in cur["checks"])


def test_old_results_without_salary_pan_check_cannot_be_approved(applicant, staff, worker):
    from sqlalchemy import delete

    from app.core.db import session_scope
    from app.models import VerificationCheck

    aid, cur = full_flow(applicant(), worker, sy.person(0))
    with session_scope() as db:
        db.execute(delete(VerificationCheck).where(VerificationCheck.version_id == cur["id"],
                                                  VerificationCheck.check_type == "PAN_NUMBER_SALARY_VS_PAN"))
        db.commit()
    reviewer = staff()
    response = reviewer.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
    })
    assert response.status_code == 409
    assert "salary slip" in response.text


def test_matching_synthetic_identifiers_are_informational_and_not_promoted(applicant, staff, worker):
    from sqlalchemy import func, select

    from app.core.db import session_scope
    from app.models import ConfirmedIdentity, IdentityClaim

    p = sy.person(0)
    p.aadhaar = "1211 4001 2003"
    p.pan = "ABCDE1234F"  # Clearly synthetic: not a valid PAN holder-type character.
    a = applicant()
    aid, cur = full_flow(a, worker, p)
    assert cur["check_status"] == "MATCH"
    assert any(c["code"] == "SALARY_PAN_AGREES" for c in cur["checks"])
    identity_checks = [c for c in cur["checks"] if c["check_type"].startswith("DUPLICATE_IDENTITY")]
    assert len(identity_checks) == 2
    assert all(c["status"] == "NOT_APPLICABLE" for c in identity_checks)
    reviewer = staff()
    detail = reviewer.get(f"/v1/staff/applications/{aid}").json()
    assert detail["approval_blockers"] == []
    assert reviewer.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
    }).status_code == 200
    with session_scope() as db:
        claims = db.scalars(select(IdentityClaim).where(IdentityClaim.application_id == aid)).all()
        assert all(c.status == "UNTRUSTED" for c in claims)
        assert db.scalar(select(func.count()).select_from(ConfirmedIdentity)) == 0


def test_different_synthetic_pan_still_mismatches(applicant, worker):
    p = sy.person(0)
    p.pan = "ABCDE1234F"
    slip = sy.salary_doc(p)
    slip.lines = ["PAN: ABCDE5678F" if line.startswith("PAN:") else line for line in slip.lines]
    _, cur = full_flow(applicant(), worker, p, slips=[slip])
    assert cur["check_status"] == "MISMATCH"
    assert any(c["code"] == "SALARY_PAN_DIFFERS" for c in cur["checks"])


def test_ocr_name_ambiguity_does_not_claim_pan_or_salary_pan_is_missing(applicant, staff, worker):
    from sqlalchemy import select

    from app.core.db import session_scope
    from app.models import ApplicationVersion, VerificationCheck

    aid, cur = full_flow(applicant(), worker, sy.person(0))
    with session_scope() as db:
        v = db.get(ApplicationVersion, cur["id"])
        v.check_status = "INCOMPLETE"
        for c in db.scalars(select(VerificationCheck).where(VerificationCheck.version_id == v.id)):
            if c.check_type in ("NAME_PAN_VS_AADHAAR", "NAME_SALARY_VS_AADHAAR"):
                c.status, c.code = "INCOMPLETE", "NAME_OCR_UNCERTAIN"
        db.commit()
    reviewer = staff()
    detail = reviewer.get(f"/v1/staff/applications/{aid}").json()
    assert len(detail["approval_blockers"]) == 1
    assert "OCR character ambiguity" in detail["approval_blockers"][0]
    assert "matching PAN card is required" not in str(detail["approval_blockers"])
    assert reviewer.post(f"/v1/staff/applications/{aid}/approve", json={
        "version_id": cur["id"], "row_version": cur["row_version"],
    }).status_code == 409
