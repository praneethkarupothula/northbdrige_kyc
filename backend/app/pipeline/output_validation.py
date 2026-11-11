"""Validate provider output against the sanitised input it was given.

Every filled field must cite a line, and the cited line must contain the value. Tokens must
exist in this attempt's vault, appear in the payload, and have the right entity type. Any
violation rejects the whole extraction - values are never repaired or invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.pipeline.llm import LlmExtraction
from app.pipeline.pii import TOKEN_RE, Sanitized, TokenVault, is_person_name

TOKEN_FIELDS = {
    "full_name": {"PERSON"},
    "date_of_birth": {"DOB"},
    "id_number": {"AADHAAR", "PAN"},
    "pan_number": {"PAN"},
    "employer_name": {"ORG"},
}
MONEY_FIELDS = ("gross_salary", "net_salary")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


class OutputRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ValidatedExtraction:
    document_type: str
    tokens: dict[str, str | None]  # field -> token
    money: dict[str, Decimal | None]
    currency: str | None
    pay_period_month: int | None
    pay_period_year: int | None
    evidence: dict[str, dict] = field(default_factory=dict)  # field -> {line, page, bbox}
    missing_fields: list[str] = field(default_factory=list)


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


def validate_output(ex: LlmExtraction, sanitized: Sanitized, vault: TokenVault, payload: str) -> ValidatedExtraction:
    payload_tokens = set(TOKEN_RE.findall(payload))
    payload_tokens = {f"[[{a}_{b}]]" for a, b in payload_tokens}
    sent_lines = {int(m.group(1)) for m in re.finditer(r"^L(\d{3}) ", payload, re.M)}

    # No stray tokens anywhere in free-text fields.
    for fname in ("currency",) + tuple(ex.missing_fields):
        if isinstance(fname, str) and "[[" in fname:
            raise OutputRejected("TOKEN_IN_FREE_TEXT")

    def evidence(fname: str, line: int | None, must_contain: str | None = None, *, digits: str | None = None):
        if line is None or line not in sent_lines:
            raise OutputRejected(f"MISSING_EVIDENCE:{fname}")
        sl = sanitized.line(line)
        if sl is None:
            raise OutputRejected(f"BAD_EVIDENCE_LINE:{fname}")
        if must_contain is not None and must_contain not in sl.text:
            raise OutputRejected(f"VALUE_NOT_ON_LINE:{fname}")
        if digits is not None and digits not in _digits(sl.text):
            raise OutputRejected(f"AMOUNT_NOT_ON_LINE:{fname}")
        return {"line": line, "page": sl.page, "bbox": list(sl.bbox)}

    tokens: dict[str, str | None] = {}
    ev: dict[str, dict] = {}
    for fname, allowed in TOKEN_FIELDS.items():
        value = getattr(ex, fname)
        if value is None:
            tokens[fname] = None
            continue
        value = value.strip()
        if not TOKEN_RE.fullmatch(value):
            raise OutputRejected(f"NOT_A_TOKEN:{fname}")
        if not vault.has(value) or value not in payload_tokens:
            raise OutputRejected(f"UNKNOWN_TOKEN:{fname}")
        if vault.type_of(value) not in allowed:
            raise OutputRejected(f"WRONG_TOKEN_TYPE:{fname}")
        ev[fname] = evidence(fname, getattr(ex, f"{fname}_line"), value)
        if fname == "full_name" and not is_person_name(vault.original(value)):
            raise OutputRejected("INVALID_PERSON_NAME:full_name")
        if fname == "full_name" and (ex.document_type in ("AADHAAR", "PAN", "SALARY_SLIP") or sanitized.holder_name_tokens):
            if len(sanitized.holder_name_tokens) != 1 or ex.full_name_line not in sanitized.holder_name_tokens.get(value, []):
                raise OutputRejected("EMPLOYEE_NAME_UNCONFIRMED:full_name")
        tokens[fname] = value

    money: dict[str, Decimal | None] = {}
    for fname in MONEY_FIELDS:
        raw = getattr(ex, fname)
        if raw is None:
            money[fname] = None
            continue
        if not re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", raw.replace(",", "").strip()):
            raise OutputRejected(f"BAD_AMOUNT:{fname}")
        try:
            amt = Decimal(raw.replace(",", "").strip())
        except InvalidOperation:
            raise OutputRejected(f"BAD_AMOUNT:{fname}") from None
        if amt <= 0:
            raise OutputRejected(f"BAD_AMOUNT:{fname}")
        whole = _digits(str(amt.quantize(Decimal(1)) if amt == amt.to_integral() else amt).split(".")[0])
        ev[fname] = evidence(fname, getattr(ex, f"{fname}_line"), digits=whole)
        money[fname] = amt.quantize(Decimal("0.01"))

    month, year = ex.pay_period_month, ex.pay_period_year
    if month is not None or year is not None:
        if month is None or year is None:
            raise OutputRejected("PARTIAL_PAY_PERIOD")
        e = evidence("pay_period", ex.pay_period_line)
        t = sanitized.line(ex.pay_period_line).text.lower()
        month_ok = _MONTHS[month - 1] in t or re.search(rf"(?<!\d)0?{month}[/\-.](?:20)?{str(year)[-2:]}", t)
        if str(year) not in t and not month_ok:
            raise OutputRejected("PAY_PERIOD_NOT_ON_LINE")
        if not month_ok:
            raise OutputRejected("PAY_PERIOD_NOT_ON_LINE")
        ev["pay_period"] = e

    currency = ex.currency.upper().strip() if ex.currency else None
    if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
        raise OutputRejected("BAD_CURRENCY")

    missing = [f for f in ex.missing_fields if f in ("full_name", "date_of_birth", "id_number", "pan_number", "employer_name",
                                                       "gross_salary", "net_salary", "pay_period")]
    return ValidatedExtraction(ex.document_type, tokens, money, currency, month, year, ev, missing)
