"""Deterministic document/name consistency rules.

* Automatic MATCH requires *exact* equality of application-scoped normalised name tokens.
* Transliteration, initials, re-ordering and fuzzy similarity only produce review *hints*.
* A confidently extracted differing name -> MISMATCH; missing/unreadable required name -> INCOMPLETE.
* Bank statements never substitute for salary slips; their name pair is NOT_APPLICABLE.
* The account-entered name is compared separately and never replaces documentary evidence.

A MATCH means the names on the submitted documents agree. It does not prove the documents are
authentic and it is not an identity verification against government records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from app.models.enums import CheckStatus, DocumentCategory


@dataclass
class DocOutcome:
    document_id: str
    category: str
    usable: bool  # extraction succeeded and was validated
    reason: str | None = None  # why not usable (UNREADABLE, PRIVACY_GATE_BLOCKED, LLM_SCHEMA_INVALID ...)
    document_type: str | None = None
    name_token: str | None = None
    name_norm: str | None = None  # normalised original - local only, used for hints, never stored
    employer_token: str | None = None
    pay_period: tuple[int, int] | None = None
    net_salary: Decimal | None = None
    gross_salary: Decimal | None = None
    content_fp: str | None = None
    page_count: int = 1
    father_name_token: str | None = None
    pan_token: str | None = None
    name_from_ocr: bool = False


@dataclass
class CheckResult:
    check_type: str
    status: str
    code: str
    document_id: str | None = None
    reference_document_id: str | None = None
    detail: dict = field(default_factory=dict)


def review_hints(a: str | None, b: str | None) -> dict:
    """Hints for a human reviewer. Contains codes and a similarity score only - no names."""
    if not a or not b:
        return {}
    pa, pb = a.split(), b.split()
    hints = []
    if sorted(pa) == sorted(pb):
        hints.append("REORDERED_NAME_PARTS")
    initials_a = [p[0] for p in pa]
    initials_b = [p[0] for p in pb]
    if (any(len(p) == 1 for p in pa + pb)) and (initials_a == initials_b or sorted(initials_a) == sorted(initials_b)):
        hints.append("INITIALS_COMPATIBLE")
    if set(pa) < set(pb) or set(pb) < set(pa):
        hints.append("NAME_PART_SUBSET")
    score = round(fuzz.token_sort_ratio(a, b), 1)
    if score >= 85 and "REORDERED_NAME_PARTS" not in hints:
        hints.append("HIGH_SIMILARITY_POSSIBLE_SPELLING_OR_TRANSLITERATION")
    if not hints:
        hints.append("DIFFERENT_NAMES")
    return {"hints": hints, "similarity": score}


def _pair(check_type: str, doc: DocOutcome, ref: DocOutcome) -> CheckResult:
    if not doc.usable or doc.name_token is None:
        return CheckResult(check_type, CheckStatus.INCOMPLETE, doc.reason or "NAME_NOT_EXTRACTED", doc.document_id,
                           ref.document_id)
    if doc.name_token == ref.name_token:
        return CheckResult(check_type, CheckStatus.MATCH, "NAMES_IDENTICAL_AFTER_NORMALISATION", doc.document_id,
                           ref.document_id)
    if (doc.name_from_ocr or ref.name_from_ocr) and doc.name_norm and ref.name_norm:
        # I/l/1 can be visually indistinguishable in raster cards. Never turn
        # this ambiguity into an automatic MATCH or a definite MISMATCH.
        def ocr_key(name):
            return name.translate(str.maketrans({"l": "i", "1": "i", "0": "o"}))
        if ocr_key(doc.name_norm) == ocr_key(ref.name_norm):
            return CheckResult(check_type, CheckStatus.INCOMPLETE, "NAME_OCR_UNCERTAIN", doc.document_id,
                               ref.document_id, {"hints": ["OCR_CHARACTER_AMBIGUITY"]})
    return CheckResult(check_type, CheckStatus.MISMATCH, "PAN_NAME_DIFFERS" if check_type == "NAME_SALARY_VS_PAN" else "NAMES_DIFFER", doc.document_id, ref.document_id,
                       review_hints(doc.name_norm, ref.name_norm))


def evaluate(
    docs: list[DocOutcome],
    *,
    pan_status: str,
    pan_policy: str,
    required_salary_slips: int,
    account_name_norm: str | None,
) -> tuple[list[CheckResult], str]:
    checks: list[CheckResult] = []
    aadhaar_docs = [d for d in docs if d.category == DocumentCategory.AADHAAR]
    pan_docs = [d for d in docs if d.category == DocumentCategory.PAN]
    slips = [d for d in docs if d.category == DocumentCategory.SALARY_SLIP]
    banks = [d for d in docs if d.category == DocumentCategory.BANK_STATEMENT]

    # ---- document type consistency -------------------------------------------------
    for d in docs:
        if d.usable and d.document_type and d.document_type != d.category:
            d.usable = False
            d.reason = "DOCUMENT_TYPE_DIFFERS_FROM_UPLOAD_SLOT"
            checks.append(CheckResult("DOCUMENT_TYPE", CheckStatus.MISMATCH, "DOCUMENT_TYPE_DIFFERS_FROM_UPLOAD_SLOT",
                                      d.document_id, detail={"classified_as": d.document_type, "uploaded_as": d.category}))

    # ---- primary identity (Aadhaar) ------------------------------------------------
    primary = aadhaar_docs[0] if aadhaar_docs else None
    if primary is None:
        checks.append(CheckResult("REQUIRED_DOCUMENT", CheckStatus.INCOMPLETE, "AADHAAR_MISSING"))
    elif not primary.usable or primary.name_token is None:
        checks.append(CheckResult("PRIMARY_IDENTITY_NAME", CheckStatus.INCOMPLETE,
                                  primary.reason or "NAME_NOT_EXTRACTED", primary.document_id))
        primary = None
    else:
        checks.append(CheckResult("PRIMARY_IDENTITY_NAME", CheckStatus.MATCH, "NAME_EXTRACTED", primary.document_id))

    # ---- PAN (optional under explicit prototype policy) ------------------------------
    if pan_docs:
        for p in pan_docs:
            if primary is None:
                checks.append(CheckResult("NAME_PAN_VS_AADHAAR", CheckStatus.INCOMPLETE, "PRIMARY_NAME_UNAVAILABLE",
                                          p.document_id))
            else:
                checks.append(_pair("NAME_PAN_VS_AADHAAR", p, primary))
    else:
        checks.append(CheckResult("REQUIRED_DOCUMENT", CheckStatus.INCOMPLETE, "PAN_REQUIRED_BY_POLICY"))
    pan_reference = pan_docs[0] if pan_docs else None

    # ---- salary slips ---------------------------------------------------------------
    seen_fp: dict[str, str] = {}
    seen_period: dict[tuple[int, int], str] = {}
    counted: list[DocOutcome] = []
    for s in slips:
        if s.content_fp and s.content_fp in seen_fp:
            checks.append(CheckResult("SALARY_SLIP_DUPLICATE", CheckStatus.NOT_APPLICABLE, "IDENTICAL_FILE_REPEATED",
                                      s.document_id, seen_fp[s.content_fp]))
            continue  # identical file: not double-counted
        if s.content_fp:
            seen_fp[s.content_fp] = s.document_id
        if primary is None:
            checks.append(CheckResult("NAME_SALARY_VS_AADHAAR", CheckStatus.INCOMPLETE, "PRIMARY_NAME_UNAVAILABLE",
                                      s.document_id))
        else:
            checks.append(_pair("NAME_SALARY_VS_AADHAAR", s, primary))
        if pan_reference is None or not pan_reference.usable or not pan_reference.name_token:
            checks.append(CheckResult("NAME_SALARY_VS_PAN", CheckStatus.INCOMPLETE, "PAN_NAME_UNAVAILABLE", s.document_id))
        else:
            checks.append(_pair("NAME_SALARY_VS_PAN", s, pan_reference))
        if not s.usable or not s.pan_token:
            checks.append(CheckResult("PAN_NUMBER_SALARY_VS_PAN", CheckStatus.INCOMPLETE,
                                      "SALARY_PAN_NOT_EXTRACTED", s.document_id,
                                      pan_reference.document_id if pan_reference else None))
        elif pan_reference is None or not pan_reference.usable or not pan_reference.pan_token:
            checks.append(CheckResult("PAN_NUMBER_SALARY_VS_PAN", CheckStatus.INCOMPLETE,
                                      "PAN_NUMBER_UNAVAILABLE", s.document_id))
        else:
            same_pan = s.pan_token == pan_reference.pan_token
            checks.append(CheckResult("PAN_NUMBER_SALARY_VS_PAN", CheckStatus.MATCH if same_pan else CheckStatus.MISMATCH,
                                      "SALARY_PAN_AGREES" if same_pan else "SALARY_PAN_DIFFERS", s.document_id,
                                      pan_reference.document_id))
        if s.usable and s.pay_period:
            if s.pay_period in seen_period:
                checks.append(CheckResult("SALARY_PAY_PERIOD", CheckStatus.NOT_APPLICABLE, "DUPLICATE_PAY_PERIOD",
                                          s.document_id, seen_period[s.pay_period],
                                          {"month": s.pay_period[0], "year": s.pay_period[1]}))
                continue  # same period counted once
            seen_period[s.pay_period] = s.document_id
        if s.usable:
            counted.append(s)
    if len(counted) < required_salary_slips:
        checks.append(CheckResult("REQUIRED_DOCUMENT", CheckStatus.INCOMPLETE, "SALARY_SLIPS_BELOW_REQUIRED",
                                  detail={"required": required_salary_slips, "usable": len(counted)}))

    # salary review information (never a fraud conclusion)
    if counted:
        nets = [s.net_salary for s in counted if s.net_salary]
        employers = {s.employer_token for s in counted if s.employer_token}
        info = {
            "periods": sorted([f"{p[1]}-{p[0]:02d}" for p in seen_period]),
            "distinct_employers": len(employers),
            "slips_counted": len(counted),
        }
        if len(nets) >= 2:
            lo, hi = min(nets), max(nets)
            info["net_variation_pct"] = float(round((hi - lo) / lo * 100, 1)) if lo else None
        checks.append(CheckResult("SALARY_REVIEW_INFO", CheckStatus.NOT_APPLICABLE,
                                  "EMPLOYER_DIFFERS" if len(employers) > 1 else "SALARY_SUMMARY", detail=info))

    # ---- bank statement (informational only) ------------------------------------------
    for b in banks:
        detail = {}
        if primary is not None and b.usable and b.name_token:
            detail["name_agrees_with_aadhaar"] = b.name_token == primary.name_token
        checks.append(CheckResult("NAME_BANK_VS_AADHAAR", CheckStatus.NOT_APPLICABLE, "BANK_STATEMENT_INFORMATIONAL",
                                  b.document_id, primary.document_id if primary else None, detail))

    # ---- account-entered name (separate, informational) --------------------------------
    if primary is not None and account_name_norm:
        same = account_name_norm == primary.name_norm
        checks.append(CheckResult("ACCOUNT_NAME_VS_AADHAAR", CheckStatus.NOT_APPLICABLE,
                                  "ACCOUNT_NAME_AGREES" if same else "ACCOUNT_NAME_DIFFERS", primary.document_id,
                                  detail={} if same else review_hints(account_name_norm, primary.name_norm)))

    return checks, aggregate(checks)


def aggregate(checks: list[CheckResult]) -> str:
    statuses = [c.status for c in checks if c.status != CheckStatus.NOT_APPLICABLE]
    if CheckStatus.MISMATCH in statuses:
        return CheckStatus.MISMATCH
    if CheckStatus.INCOMPLETE in statuses:
        return CheckStatus.INCOMPLETE
    return CheckStatus.MATCH
