"""Local identifier normalisation and *plausibility* checks.

These checks only test format (and, for Aadhaar, the Verhoeff check digit). They do NOT
verify a document against UIDAI or Income Tax Department records, and values are never
modified to make them pass.
"""

from __future__ import annotations

import re
import unicodedata

# Verhoeff tables
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


AADHAAR_RE = re.compile(r"(?<!\d)([2-9]\d{3})[ \-]?(\d{4})[ \-]?(\d{4})(?!\d)")
PAN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z])(?![A-Z0-9])")
PAN_LOOSE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{5}\d{4}[A-Za-z])(?![A-Za-z0-9])")


def canonical_aadhaar(value: str) -> str:
    return re.sub(r"\D", "", value)


def canonical_pan(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def aadhaar_plausible(canonical: str) -> tuple[bool, str]:
    if len(canonical) != 12:
        return False, "AADHAAR_LENGTH"
    if canonical[0] in "01":
        return False, "AADHAAR_LEADING_DIGIT"
    if not verhoeff_valid(canonical):
        return False, "AADHAAR_CHECKSUM"
    return True, "AADHAAR_FORMAT_PLAUSIBLE"


def pan_plausible(canonical: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", canonical):
        return False, "PAN_FORMAT"
    if canonical[3] not in "ABCFGHLJPTK":
        return False, "PAN_HOLDER_TYPE"
    return True, "PAN_FORMAT_PLAUSIBLE"


def mask_aadhaar(canonical: str) -> str:
    return "XXXX XXXX " + canonical[-4:] if len(canonical) >= 4 else "XXXX"


def mask_pan(canonical: str) -> str:
    return "XXXXX" + canonical[5:9] + "X" if len(canonical) == 10 else "XXXXXXXXXX"


def normalize_name(name: str) -> str:
    """Unicode NFKC + case folding + whitespace collapsing + removal of punctuation."""
    s = unicodedata.normalize("NFKC", name).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def mask_name(name: str) -> str:
    parts = name.split()
    return " ".join(p[0] + "*" * max(1, len(p) - 1) for p in parts) if parts else "***"
