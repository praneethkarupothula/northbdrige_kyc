"""Acceptance 4-9, 14 and salary/bank rules via the full pipeline (text-layer PDFs)."""

from app import synthetic as sy
from app.pipeline.matching import DocOutcome, evaluate, review_hints
from tests.helpers import checks_by_type, full_flow, start, submit, upload_doc


def test_aadhaar_and_one_slip_match(applicant, worker):
    _, cur = full_flow(applicant(), worker, sy.person(0))
    assert cur["check_status"] == "MATCH"
    c = checks_by_type(cur)
    assert c["NAME_SALARY_VS_AADHAAR"][0]["status"] == "MATCH"
    assert c["NAME_PAN_VS_AADHAAR"][0]["status"] == "MATCH"
    assert cur["pan_status"] == "SUPPLIED"


def test_multiple_slips_match(applicant, worker):
    p = sy.person(1)
    slips = [sy.salary_doc(p, sy.SalaryInfo(month=m)) for m in ("January", "February", "March")]
    _, cur = full_flow(applicant("Rohan Iyer"), worker, p, slips=slips)
    assert cur["check_status"] == "MATCH"
    assert [c["status"] for c in checks_by_type(cur)["NAME_SALARY_VS_AADHAAR"]] == ["MATCH"] * 3
    info = checks_by_type(cur)["SALARY_REVIEW_INFO"][0]["detail"]
    assert info["periods"] == ["2025-01", "2025-02", "2025-03"] and info["slips_counted"] == 3


def test_pan_supplied_matching(applicant, worker):
    _, cur = full_flow(applicant(), worker, sy.person(0), pan=True)
    assert cur["pan_status"] == "SUPPLIED"
    assert checks_by_type(cur)["NAME_PAN_VS_AADHAAR"][0]["status"] == "MATCH"
    assert cur["check_status"] == "MATCH"


def test_pan_conflicting_with_aadhaar(applicant, worker):
    from tests.helpers import supply_pan  # noqa: F401
    from app.workers.runner import drain

    p = sy.person(0)
    api = applicant()
    aid = start(api)["id"]
    upload_doc(api, aid, sy.aadhaar_doc(p))
    other = sy.person(2)
    pan = sy.pan_doc(sy.Person(name=other.name, dob=p.dob, aadhaar=p.aadhaar, pan=p.pan, father=other.father))
    upload_doc(api, aid, pan)
    upload_doc(api, aid, sy.salary_doc(p))
    submit(api, aid)
    drain(worker)
    cur = api.get(f"/v1/applications/{aid}").json()["current"]
    assert cur["check_status"] == "MISMATCH"
    pc = checks_by_type(cur)["NAME_PAN_VS_AADHAAR"][0]
    assert pc["status"] == "MISMATCH"
    pan_doc = next(d for d in cur["documents"] if d["category"] == "PAN")
    assert pan_doc["attention"] and "does not match" in pan_doc["attention"]
    assert "hints" not in pc["detail"], "applicant view hides reviewer hints"


def test_one_mismatched_slip_among_matching(applicant, worker):
    p = sy.person(0)
    slips = [sy.salary_doc(p, sy.SalaryInfo(month="January")), sy.salary_doc(p, sy.SalaryInfo(month="February"),
             name_override="Asha Varma"), sy.salary_doc(p, sy.SalaryInfo(month="March"))]
    _, cur = full_flow(applicant(), worker, p, slips=slips)
    assert cur["check_status"] == "MISMATCH"
    statuses = [c["status"] for c in checks_by_type(cur)["NAME_SALARY_VS_AADHAAR"]]
    assert statuses == ["MATCH", "MISMATCH", "MATCH"], "per-document results are kept"
    assert cur["status"]["key"] == "MISMATCH"


def test_same_name_different_ids_not_duplicate(applicant, worker):
    a, b = sy.person(0), sy.person(0, seed=99)  # same name, different identifiers
    assert a.name == b.name and a.aadhaar != b.aadhaar
    _, ca = full_flow(applicant(), worker, a)
    _, cb = full_flow(applicant(), worker, b)
    assert ca["check_status"] == cb["check_status"] == "MATCH"
    assert checks_by_type(cb)["DUPLICATE_IDENTITY_AADHAAR"][0]["code"] == "NO_CONFLICTING_CLAIM"


def test_identical_slip_not_double_counted_and_duplicate_period(applicant, worker):
    from app.workers.runner import drain
    from tests.helpers import current, supply_pan, upload

    p = sy.person(0)
    api = applicant()
    aid = start(api)["id"]
    upload_doc(api, aid, sy.aadhaar_doc(p))
    supply_pan(api, aid)
    same_bytes = sy.to_pdf_text(sy.salary_doc(p))
    assert upload(api, aid, "SALARY_SLIP", same_bytes).status_code == 201
    r = upload(api, aid, "SALARY_SLIP", same_bytes)
    assert r.status_code == 201 and r.json()["duplicate_of"], "repeat upload is flagged immediately"
    upload_doc(api, aid, sy.salary_doc(p, sy.SalaryInfo(basic=31000)))  # different file, same period
    submit(api, aid)
    drain(worker)
    cur = current(api, aid)
    c = checks_by_type(cur)
    assert c["SALARY_SLIP_DUPLICATE"][0]["code"] == "IDENTICAL_FILE_REPEATED"
    assert c["SALARY_PAY_PERIOD"][0]["code"] == "DUPLICATE_PAY_PERIOD"
    assert c["SALARY_REVIEW_INFO"][0]["detail"]["slips_counted"] == 1
    assert cur["check_status"] == "MATCH", "duplicates are review information, not a mismatch"


def test_bank_statement_is_informational(applicant, worker):
    _, cur = full_flow(applicant(), worker, sy.person(0), bank=True)
    bank = checks_by_type(cur)["NAME_BANK_VS_AADHAAR"][0]
    assert bank["status"] == "NOT_APPLICABLE" and bank["detail"]["name_agrees_with_aadhaar"] is True
    assert cur["check_status"] == "MATCH"
    bdoc = next(d for d in cur["documents"] if d["category"] == "BANK_STATEMENT")
    assert bdoc["pages_processed"] == [1]


# ------------------------------------------------------------------ rule-level unit tests
def _d(i, cat, tok, norm=None, **kw):
    return DocOutcome(f"d{i}", cat, usable=tok is not None or kw.pop("usable", False), name_token=tok,
                      name_norm=norm, document_type=cat, **kw)


def test_rules_missing_required_name_incomplete():
    docs = [_d(1, "AADHAAR", None, reason="UNREADABLE"), _d(2, "SALARY_SLIP", "[[PERSON_1]]", "asha verma")]
    _, agg = evaluate(docs, pan_status="UNAVAILABLE", pan_policy="optional", required_salary_slips=1, account_name_norm=None)
    assert agg == "INCOMPLETE"
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]", "asha verma"), _d(2, "SALARY_SLIP", None, reason="UNREADABLE")]
    checks, agg = evaluate(docs, pan_status="UNAVAILABLE", pan_policy="optional", required_salary_slips=1,
                           account_name_norm=None)
    assert agg == "INCOMPLETE"


def test_rules_missing_pan_is_incomplete_even_for_legacy_optional_policy():
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]"), _d(2, "SALARY_SLIP", "[[PERSON_1]]")]
    for status, policy in [("UNAVAILABLE", "optional"), ("NOT_REQUESTED", "not_requested"), ("PENDING", "required")]:
        assert evaluate(docs, pan_status=status, pan_policy=policy, required_salary_slips=1,
                        account_name_norm=None)[1] == "INCOMPLETE"


def test_rules_bank_cannot_replace_salary_slip():
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]", "a b"), _d(2, "BANK_STATEMENT", "[[PERSON_1]]", "a b")]
    assert evaluate(docs, pan_status="UNAVAILABLE", pan_policy="optional", required_salary_slips=1,
                    account_name_norm=None)[1] == "INCOMPLETE"


def test_account_name_does_not_replace_documents():
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]", "asha verma"), _d(2, "SALARY_SLIP", "[[PERSON_1]]", "asha verma")]
    docs.append(_d(3, "PAN", "[[PERSON_1]]", pan_token="[[PAN_1]]"))
    docs[1].pan_token = "[[PAN_1]]"
    checks, agg = evaluate(docs, pan_status="SUPPLIED", pan_policy="required", required_salary_slips=1,
                           account_name_norm="someone else")
    assert agg == "MATCH"
    assert any(c.code == "ACCOUNT_NAME_DIFFERS" and c.status == "NOT_APPLICABLE" for c in checks)


def test_review_hints_are_hints_only():
    assert "REORDERED_NAME_PARTS" in review_hints("verma asha", "asha verma")["hints"]
    assert "INITIALS_COMPATIBLE" in review_hints("a verma", "asha verma")["hints"]
    assert "HIGH_SIMILARITY_POSSIBLE_SPELLING_OR_TRANSLITERATION" in review_hints("asha varma", "asha verma")["hints"]
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]", "asha verma"), _d(2, "SALARY_SLIP", "[[PERSON_2]]", "verma asha")]
    _, agg = evaluate(docs, pan_status="UNAVAILABLE", pan_policy="optional", required_salary_slips=1, account_name_norm=None)
    assert agg == "MISMATCH", "reordered names are a hint, never an automatic MATCH"


def test_father_names_do_not_affect_the_holder_name_match():
    docs = [_d(1, "AADHAAR", "[[PERSON_1]]", father_name_token="[[PERSON_2]]"),
            _d(2, "PAN", "[[PERSON_1]]", father_name_token="[[PERSON_3]]"),
            _d(3, "SALARY_SLIP", "[[PERSON_1]]", father_name_token="[[PERSON_4]]")]
    docs[1].pan_token = docs[2].pan_token = "[[PAN_1]]"
    checks, result = evaluate(docs, pan_status="SUPPLIED", pan_policy="required",
                              required_salary_slips=1, account_name_norm=None)
    assert result == "MATCH"
    assert not any(c.check_type.startswith("FATHER") for c in checks)
    docs[2].name_token = "[[PERSON_5]]"
    assert evaluate(docs, pan_status="SUPPLIED", pan_policy="optional",
                    required_salary_slips=1, account_name_norm=None)[1] == "MISMATCH"
