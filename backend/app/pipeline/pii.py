"""PII detection, tokenisation and the outbound privacy gate.

Three layers run locally before anything can leave the trusted backend:

1. **Detection** - Microsoft Presidio (spaCy NER + built-in recognisers) plus custom
   recognisers for Indian identifiers and document-specific *label* detection
   ("Employee Name:", "DOB:", "Address:" ...), plus layout heuristics for ID cards.
2. **Tokenisation** - detected values become placeholder tokens such as ``[[PERSON_1]]``
   from a :class:`TokenVault` scoped to one application-processing attempt. Identical
   normalised names map to the same token; different names to different tokens.
3. **Minimisation + privacy gate** - remaining words that are not on a payroll/ID vocabulary
   allowlist are replaced with ``[REDACTED]``. The gate then re-scans for residual
   identifiers and checks that no vault original appears in the payload. Anything uncertain
   is blocked and routed to manual review. "Presidio found nothing" is never treated as proof
   of safety on its own.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from app.pipeline.identifiers import canonical_aadhaar, canonical_pan, normalize_name
from app.pipeline.ocr import Line

TOKEN_RE = re.compile(r"\[\[([A-Z]+)_(\d+)\]\]")
REDACTED = "[REDACTED]"


# =============================================================================== vault
class VaultCleared(RuntimeError):
    pass


class TokenVault:
    """In-memory token <-> original mapping for ONE application-processing attempt.

    Never persisted, logged, queued or sent to the browser. ``clear()`` is called from a
    ``finally`` block by the orchestrator. (Python cannot guarantee physical zeroisation of
    the underlying string memory; clearing drops all references.)
    """

    def __init__(self, scope: str):
        self.scope = scope
        self._by_key: dict[tuple[str, str], str] = {}
        self._original: dict[str, str] = {}
        self._type: dict[str, str] = {}
        self._min_conf: dict[str, float | None] = {}
        self._counters: Counter[str] = Counter()
        self.cleared = False

    @staticmethod
    def _key(etype: str, value: str) -> str:
        if etype == "PERSON":
            return normalize_name(value)
        if etype == "AADHAAR":
            return canonical_aadhaar(value) or normalize_name(value)
        if etype == "PAN":
            return canonical_pan(value)
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()

    def token_for(self, etype: str, original: str, conf: float | None = None) -> str:
        if self.cleared:
            raise VaultCleared("vault already cleared")
        k = (etype, self._key(etype, original))
        tok = self._by_key.get(k)
        if tok is None:
            self._counters[etype] += 1
            tok = f"[[{etype}_{self._counters[etype]}]]"
            self._by_key[k] = tok
            self._original[tok] = original.strip()
            self._type[tok] = etype
            self._min_conf[tok] = conf
        elif conf is not None:
            prev = self._min_conf.get(tok)
            self._min_conf[tok] = conf if prev is None else min(prev, conf)
        return tok

    def has(self, token: str) -> bool:
        return token in self._original

    def original(self, token: str) -> str:
        if self.cleared:
            raise VaultCleared("vault already cleared")
        return self._original[token]

    def type_of(self, token: str) -> str:
        return self._type[token]

    def min_conf(self, token: str) -> float | None:
        return self._min_conf.get(token)

    def originals(self) -> list[str]:
        return list(self._original.values())

    def __len__(self) -> int:
        return len(self._original)

    def clear(self) -> None:
        self._by_key.clear()
        self._original.clear()
        self._type.clear()
        self._min_conf.clear()
        self._counters.clear()
        self.cleared = True

    def __enter__(self) -> TokenVault:
        return self

    def __exit__(self, *exc) -> None:
        self.clear()


# ======================================================================== vocabulary
# Words that may leave the backend un-redacted. Anything else alphabetic becomes [REDACTED].
SAFE_VOCAB = set(
    """
    government of india unique identification authority aadhaar aadhar uid uidai enrolment enrollment
    vid virtual id number no card your my is the a an and or for to on at in by with from as per
    income tax department govt permanent account pan signature photo name names holder holders
    father fathers father's mother spouse husband guardian s/o d/o w/o c/o dob date birth year yob
    male female transgender gender sex address addr pin pincode code mobile phone tel email e-mail
    issue issued download print valid validity till
    salary slip payslip pay pay-slip statement month period payroll employee employer emp company
    organisation organization establishment details detail designation department dept grade location
    branch joining doj bank bank's ifsc micr a/c ac acct type savings current customer cif
    earnings earning deductions deduction gross net total amount amounts rs rs. inr ₹ rupees only
    basic hra da dearness allowance allowances house rent conveyance special medical transport lta
    leave travel bonus incentive overtime arrears reimbursement other others misc variable fixed
    provident fund pf epf vpf uan esi esic professional tax pt tds income-tax lwf loan advance
    recovery insurance gratuity nps contribution employer's employee's ctc take home takehome
    days working worked paid lop loss present absent payable payment mode credited credit debit
    transfer neft imps rtgs upi cheque cash withdrawal deposit balance opening closing brought forward
    transaction transactions txn particulars description narration ref reference value chq
    january february march april may june july august september october november december
    jan feb mar apr jun jul aug sep sept oct nov dec
    monday tuesday wednesday thursday friday saturday sunday
    page summary computer generated document does not require this synthetic sample
    specimen test fixture real valid only use testing evaluation demo
    words lakh lakhs thousand hundred crore zero one two three four five six seven eight nine ten
    eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty
    fifty sixty seventy eighty ninety
    """.split()
)


def _is_safe_word(w: str) -> bool:
    stripped = w.strip(".,:;()")
    if TOKEN_RE.fullmatch(stripped) or stripped == REDACTED or w.startswith(REDACTED):
        return True
    core = w.strip(".,:;()[]{}|/\\-–—'\"*#").casefold()
    if not core:
        return True
    if not re.search(r"[^\W\d_]", core):  # no letters -> numeric/punctuation, handled separately
        return True
    if core in SAFE_VOCAB:
        return True
    parts = re.split(r"[/\-]", core)
    return all(p in SAFE_VOCAB or not p for p in parts)


# ===================================================================== label detection
_LABELS: list[tuple[str, str]] = [
    (r"father'?s?\s+name|mother'?s?\s+name|spouse'?s?\s+name|husband'?s?\s+name|guardian(?:'?s?\s+name)?|s/o|d/o|w/o|c/o", "PERSON"),
    (r"employee\s+name|emp(?:loyee)?\.?\s*name|name\s+of\s+(?:the\s+)?employee|account\s+holder(?:'?s?\s+name)?|customer\s+name|holder'?s?\s+name|applicant\s+name|full\s+name|name", "PERSON"),
    (r"date\s+of\s+birth|d\.?\s?o\.?\s?b\.?|year\s+of\s+birth|birth\s+date|yob", "DOB"),
    (r"(?:residential\s+|permanent\s+|communication\s+)?address|addr\.?", "ADDRESS"),
    (r"employer(?:\s+name)?|company(?:\s+name)?|organi[sz]ation(?:\s+name)?|establishment(?:\s+name)?", "ORG"),
    (r"employee\s+(?:id|code|no\.?|number)|emp\.?\s*(?:id|code|no\.?)|staff\s+id", "EMPID"),
    (r"(?:savings\s+|current\s+)?a/?c\.?\s*(?:no\.?|number)|account\s+(?:no\.?|number)|customer\s+id|cif(?:\s+no\.?)?", "ACCOUNT"),
    (r"uan(?:\s+no\.?)?|pf\s*(?:a/c\s*)?(?:no\.?|number)|esic?\s*(?:no\.?|number)|policy\s*(?:no\.?|number)", "ID"),
    (r"mobile(?:\s+no\.?|\s+number)?|phone(?:\s+no\.?|\s+number)?|tel\.?|contact(?:\s+no\.?)?", "PHONE"),
    (r"e-?mail(?:\s+id)?", "EMAIL"),
    (r"pin\s*code|pincode", "ADDRESS"),
    (r"(?:aadhaar|aadhar|uid)(?:\s+(?:no\.?|number))?|vid", "AADHAAR"),
    (r"permanent\s+account\s+number(?:\s+card)?|pan(?:\s+(?:no\.?|number))?", "PAN"),
    (r"ifsc(?:\s+code)?|micr(?:\s+code)?", "IFSC"),
    (r"branch(?:\s+name)?", "LOCATION"),
]
_LABEL_ALT = "|".join(f"(?P<L{i}>{pat})" for i, (pat, _) in enumerate(_LABELS))
_LABEL_WITH_SEP = re.compile(rf"(?i)(?<![\w/])(?:{_LABEL_ALT})\s*(?::|-(?=\s)|–)\s*")
_LABEL_ALONE = re.compile(rf"(?i)^\s*(?:{_LABEL_ALT})\s*[:\-]?\s*$")
SENSITIVE_LABEL_TYPES = {"PERSON", "DOB", "AADHAAR", "PAN", "ACCOUNT", "PHONE", "EMAIL", "ADDRESS", "EMPID", "ID", "ORG"}


def _standalone_label(text: str):
    match = _LABEL_ALONE.match(text)
    if match:
        return match
    # Indian cards commonly put a local-language label before / Name or | DOB.
    for suffix in re.split(r"[/|]", text)[1:]:
        match = _LABEL_ALONE.match(suffix.strip())
        if match:
            return match
    return None


def _label_type(m: re.Match) -> str:
    for i, (_, etype) in enumerate(_LABELS):
        if m.group(f"L{i}") is not None:
            return etype
    return "ID"


@dataclass
class Span:
    line: int
    start: int
    end: int
    etype: str
    priority: int  # 3 label/layout, 2 custom regex, 1 NER
    holder_name: bool = False
    father_name: bool = False


def _father_label(match: re.Match) -> bool:
    return match.group("L0") is not None and bool(re.match(r"(?i)(?:father|s/o|d/o)", match.group("L0")))


def is_person_name(value: str) -> bool:
    return bool(re.search(r"[^\W\d_]", value)) and not bool(re.search(r"[\d@]", value))


def _name_bounds(text: str):
    # Portrait/QR graphics can become leading punctuation on an otherwise valid
    # OCR name line. Keep the exact name span; never remove alphabetic words.
    match = re.fullmatch(r"[\W_]*?([A-Za-z][A-Za-z.'\- ]{2,60})[\W_]*", text)
    return match.span(1) if match else None


def _label_spans(lines: list[str], layout: list[Line] | None = None, category: str | None = None) -> list[Span]:
    spans: list[Span] = []
    for li, text in enumerate(lines):
        matches = list(_LABEL_WITH_SEP.finditer(text))
        for j, m in enumerate(matches):
            end = matches[j + 1].start() if j + 1 < len(matches) else len(text)
            vs, ve = m.end(), end
            while ve > vs and text[ve - 1] in " \t|,;":
                ve -= 1
            if ve > vs:
                if _label_type(m) == "PERSON":
                    bounds = _name_bounds(text[vs:ve])
                    if bounds:
                        start, end = bounds
                        vs, ve = vs + start, vs + end
                spans.append(Span(li, vs, ve, _label_type(m), 3, m.group("L1") is not None, _father_label(m)))
        alone = _standalone_label(text)
        if alone and (layout is not None or (li + 1 < len(lines) and lines[li + 1].strip())):
            next_index = li + 1
            if layout:
                source = layout[li]
                x0, y0, x1, y1 = source.bbox
                candidates = []
                for idx, candidate in enumerate(layout):
                    if idx == li or candidate.page != source.page or not candidate.text.strip():
                        continue
                    if _standalone_label(candidate.text) or _LABEL_WITH_SEP.search(candidate.text):
                        continue
                    if _label_type(alone) == "PERSON" and not is_person_name(candidate.text):
                        continue
                    cx0, cy0, _, _ = candidate.bbox
                    # Close value to the right, or a value directly beneath the
                    # heading in the same column. PDF order can interleave columns.
                    if abs(cy0 - y0) < 0.008 and 0 <= cx0 - x1 <= 0.12:
                        candidates.append((0, cx0 - x1, idx))
                    elif 0 <= cy0 - y1 <= (0.10 if category == "PAN" and _label_type(alone) == "PERSON" else 0.05) and abs(cx0 - x0) <= (0.12 if category == "PAN" and _label_type(alone) == "PERSON" else 0.04):
                        candidates.append((1, cy0 - y1, idx))
                if candidates:
                    next_index = min(candidates)[2]
                elif li + 1 >= len(layout) or layout[li + 1].bbox != source.bbox:
                    continue  # Don't guess from unrelated text in another column.
            nxt = lines[next_index]
            if not _LABEL_WITH_SEP.search(nxt) and not _standalone_label(nxt):
                bounds = _name_bounds(nxt) if _label_type(alone) == "PERSON" else None
                start, end = bounds if bounds else (0, len(nxt))
                spans.append(Span(next_index, start, end, _label_type(alone), 3, alone.group("L1") is not None, _father_label(alone)))
    return spans


_HEADER_WORDS = {"government", "india", "income", "tax", "department", "govt", "permanent", "account",
                 "number", "card", "unique", "identification", "authority", "of", "synthetic", "sample",
                 "specimen", "not", "a", "real", "document", "aadhaar", "male", "female", "signature",
                 "for", "testing", "only", "test"}
_NAMEISH = re.compile(r"^[A-Za-z][A-Za-z.'\- ]{2,60}$")


def _layout_spans(lines: list[str], category: str, existing: list[Span], layout: list[Line] | None = None) -> list[Span]:
    """Locate unlabelled holder-name evidence using document layout anchors.

    Tokens remain subject to evidence validation before name comparison.
    """
    if category not in ("AADHAAR", "PAN") or any(s.holder_name for s in existing):
        return []
    out: list[Span] = []
    dob_lines = [s.line for s in existing if s.etype == "DOB"]
    candidates = {}
    for i, text in enumerate(lines):
        bounds = _name_bounds(text)
        if not bounds or _LABEL_WITH_SEP.search(text) or _standalone_label(text):
            continue
        start, end = bounds
        name = text[start:end]
        if 2 <= len(name.split()) <= 5 and not {w.casefold().strip(".") for w in name.split()} <= _HEADER_WORDS:
            candidates[i] = bounds
    if category == "AADHAAR" and dob_lines:
        before = [i for i in candidates if i < dob_lines[0]]
        if before:
            out.append(Span(before[-1], *candidates[before[-1]], "PERSON", 3, holder_name=True))
    elif category == "PAN":
        # Old PAN layouts place names before the number. PDF object order may
        # differ from visual reading order, so use page coordinates.
        order = sorted(range(len(lines)), key=lambda i: (layout[i].page, layout[i].bbox[1], layout[i].bbox[0])) if layout else list(range(len(lines)))
        rank = {i: n for n, i in enumerate(order)}
        headers = [i for i in order if re.search(r"(?i)income\s+tax\s+department|gov(?:ernment|t)\.?\s+of\s+india", lines[i])]
        anchors = headers or [s.line for s in existing if s.etype == "PAN"]
        if anchors:
            anchor = anchors[0]
            page = layout[anchor].page if layout else None
            boundaries = [i for i in order if rank[i] > rank[anchor]
                          and (not layout or layout[i].page == page)
                          and (re.search(r"(?i)father|signature|date\s+of\s+birth", lines[i]) or i in dob_lines)]
            end = min((rank[i] for i in boundaries), default=len(lines))
            relatives = {s.line for s in existing if s.father_name}
            holder = [i for i in order if i in candidates and i not in relatives
                      and rank[anchor] < rank[i] < end
                      and (not layout or layout[i].page == page)
                      and not any(w.casefold().strip(".") in _HEADER_WORDS for w in lines[i].split())]
            if holder:
                i = holder[0]
                out.append(Span(i, *candidates[i], "PERSON", 3, holder_name=True))
    return out


_ORG_SUFFIX = re.compile(
    r"(?i)\b[\w&.,'\- ]{2,80}?\b(?:pvt\.?|private|ltd\.?|limited|llp|inc\.?|corp(?:oration)?|technologies|solutions|"
    r"services|industries|enterprises)\b\.?(?:\s+(?:ltd\.?|limited))?"
)

_REGEXES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<![\w@])[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "EMAIL"),
    (re.compile(r"(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)"), "AADHAAR"),
    (re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{4}(?!\d)"), "AADHAAR"),  # 16-digit VID
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])"), "PAN"),
    (re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])"), "IFSC"),
    (re.compile(r"(?<!\d)(?:\+91[\-\s]?|0)?[6-9]\d{4}[\-\s]?\d{5}(?!\d)"), "PHONE"),
    (re.compile(r"(?<![\d,.])\d{9,18}(?![\d,.])"), "ACCOUNT"),
    (re.compile(r"(?<![A-Za-z0-9])[Xx*]{2,}[\dXx* ]*\d{3,}(?!\d)"), "ACCOUNT"),  # pre-masked numbers
]
_DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[/\-.]\d{1,2}[/\-.](?:18|19|20)\d{2}|(?:18|19|20)\d{2}[/\-.]\d{1,2}[/\-.]\d{1,2})(?!\d)")
_DIGITS_RE = re.compile(r"(?<![\d,.])\d{4,}(?![\d,.])")


def _regex_spans(lines: list[str], category: str) -> list[Span]:
    spans: list[Span] = []
    for li, text in enumerate(lines):
        if category == "AADHAAR":
            # Extraction must retain printed sample/invalid numbers too. Format
            # and checksum validity are checked later by identifiers.py.
            for m in re.finditer(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?\d{4}(?!\d)", text):
                spans.append(Span(li, m.start(), m.end(), "AADHAAR", 2))
        for rx, etype in _REGEXES:
            for m in rx.finditer(text):
                spans.append(Span(li, m.start(), m.end(), etype, 2))
        if category in ("SALARY_SLIP", "BANK_STATEMENT"):
            for m in _ORG_SUFFIX.finditer(text):
                spans.append(Span(li, m.start(), m.end(), "ORG", 2))
        if category in ("AADHAAR", "PAN"):
            for m in _DATE_RE.finditer(text):  # identity cards: any full date is treated as DOB
                spans.append(Span(li, m.start(), m.end(), "DOB", 2))
    return spans


# ---------------------------------------------------------------- Presidio (lazy, cached)
@lru_cache(maxsize=1)
def get_analyzer():
    """Build Presidio once per process. Called at worker start-up (never inside an HTTP request).

    Requires the spaCy model ``en_core_web_sm`` to be installed locally (see docs/SETUP.md).
    No model is downloaded at runtime.
    """
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    })
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.load_predefined_recognizers(languages=["en"])
    custom = [
        PatternRecognizer(supported_entity="IN_AADHAAR", name="in_aadhaar_custom",
                          patterns=[Pattern("aadhaar", r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b", 0.6)],
                          context=["aadhaar", "aadhar", "uid", "uidai"]),
        PatternRecognizer(supported_entity="IN_PAN", name="in_pan_custom",
                          patterns=[Pattern("pan", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.7)], context=["pan", "income", "tax"]),
        PatternRecognizer(supported_entity="IN_IFSC", name="in_ifsc",
                          patterns=[Pattern("ifsc", r"\b[A-Z]{4}0[A-Z0-9]{6}\b", 0.6)], context=["ifsc"]),
        PatternRecognizer(supported_entity="IN_PHONE", name="in_phone",
                          patterns=[Pattern("in_mobile", r"(?:\+91[\-\s]?)?\b[6-9]\d{9}\b", 0.6)]),
        PatternRecognizer(supported_entity="ACCOUNT_NUMBER", name="account_number",
                          patterns=[Pattern("acct", r"\b\d{9,18}\b", 0.4)],
                          context=["account", "a/c", "acct", "uan", "pf", "policy"]),
    ]
    for r in custom:
        registry.add_recognizer(r)
    return AnalyzerEngine(nlp_engine=provider.create_engine(), registry=registry, supported_languages=["en"])


_PRESIDIO_MAP = {
    "PERSON": "PERSON", "LOCATION": "LOCATION", "EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE",
    "IN_PHONE": "PHONE", "CREDIT_CARD": "ACCOUNT", "IBAN_CODE": "ACCOUNT", "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN", "IN_IFSC": "IFSC", "ACCOUNT_NUMBER": "ACCOUNT", "URL": "URL", "IP_ADDRESS": "ID",
}


def _presidio_spans(lines: list[str]) -> list[Span]:
    text = "\n".join(lines)
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    results = get_analyzer().analyze(text=text, language="en", entities=list(_PRESIDIO_MAP), score_threshold=0.5)
    spans: list[Span] = []
    for r in results:
        etype = _PRESIDIO_MAP.get(r.entity_type)
        if not etype:
            continue
        for li, off in enumerate(offsets):
            ls, le = off, off + len(lines[li])
            s, e = max(r.start, ls), min(r.end, le)
            if s < e:
                frag = text[s:e]
                words = {w.casefold().strip(".,:") for w in frag.split() if re.search(r"[^\W\d_]", w)}
                if etype in ("PERSON", "LOCATION") and words <= (SAFE_VOCAB | _HEADER_WORDS):
                    continue  # only allowlisted vocabulary/numbers - not a name
                if etype in ("PERSON", "LOCATION"):
                    while e > s and not re.match(r"[^\W\d_]", text[e - 1]):
                        e -= 1  # never let a NER name span swallow trailing amounts
                    if e <= s:
                        continue
                spans.append(Span(li, s - ls, e - ls, etype, 1))
    return spans


# ===================================================================== sanitisation
@dataclass
class SanitizedLine:
    index: int
    page: int
    bbox: tuple[float, float, float, float]
    text: str


@dataclass
class Sanitized:
    lines: list[SanitizedLine]
    entity_counts: dict[str, int]
    redaction_boxes: list[dict]  # [{page, bbox, entity}] - coordinates only, no values
    token_lines: dict[str, list[int]] = field(default_factory=dict)
    redacted_words: int = 0
    holder_name_tokens: dict[str, list[int]] = field(default_factory=dict)
    father_name_tokens: dict[str, list[int]] = field(default_factory=dict)

    def payload(self, max_chars: int) -> str:
        out, total = [], 0
        for ln in self.lines:
            row = f"L{ln.index:03d} [p{ln.page}]: {ln.text}"
            if total + len(row) > max_chars:
                break
            out.append(row)
            total += len(row) + 1
        return "\n".join(out)

    def line(self, index: int) -> SanitizedLine | None:
        for ln in self.lines:
            if ln.index == index:
                return ln
        return None


def _resolve(spans: list[Span]) -> list[Span]:
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (-s.priority, -(s.end - s.start))):
        if s.end <= s.start:
            continue
        if any(c.line == s.line and not (s.end <= c.start or s.start >= c.end) for c in chosen):
            continue
        chosen.append(s)
    return chosen


def sanitize(lines: list[Line], category: str, vault: TokenVault, *, use_presidio: bool = True) -> Sanitized:
    texts = [ln.text for ln in lines]
    spans = _label_spans(texts, lines, category)
    spans += _regex_spans(texts, category)
    spans += _layout_spans(texts, category, spans, lines)
    if use_presidio:
        spans += _presidio_spans(texts)
    chosen = _resolve(spans)

    counts: Counter[str] = Counter()
    boxes: list[dict] = []
    token_lines: dict[str, list[int]] = {}
    out_lines: list[SanitizedLine] = []
    redacted = 0
    holder_name_tokens: dict[str, list[int]] = {}
    father_name_tokens: dict[str, list[int]] = {}
    by_line: dict[int, list[Span]] = {}
    for s in chosen:
        by_line.setdefault(s.line, []).append(s)

    for li, line in enumerate(lines):
        text = texts[li]
        word_spans = line.word_spans()
        pieces: list[str] = []
        cursor = 0
        for s in sorted(by_line.get(li, []), key=lambda s: s.start):
            # trim punctuation/whitespace from the span edges
            st, en = s.start, s.end
            while st < en and text[st] in " .,:;|-":
                st += 1
            while en > st and text[en - 1] in " .,:;|-":
                en -= 1
            value = re.sub(r"\s+", " ", text[st:en])
            if not value or not re.search(r"\w", value):
                continue
            covered = [w for (a, b, w) in word_spans if not (b <= st or a >= en)]
            confs = [w.conf for w in covered if w.conf is not None]
            etype = "ID" if s.etype == "PERSON" and not is_person_name(value) else s.etype
            tok = vault.token_for(etype, value, min(confs) if confs else None)
            counts[etype] += 1
            token_lines.setdefault(tok, []).append(li)
            if s.father_name:
                father_name_tokens.setdefault(tok, []).append(li)
            if s.holder_name and etype == "PERSON":
                holder_name_tokens.setdefault(tok, []).append(li)
            if covered:
                boxes.append({
                    "page": line.page, "entity": etype,
                    "bbox": [min(w.bbox[0] for w in covered), min(w.bbox[1] for w in covered),
                             max(w.bbox[2] for w in covered), max(w.bbox[3] for w in covered)],
                })
            pieces.append(text[cursor:st])
            pieces.append(tok)
            cursor = en
        pieces.append(text[cursor:])
        masked = "".join(pieces)
        # Minimisation: allowlisted vocabulary, tokens, numbers and punctuation only.
        words_out = []
        for w in masked.split():
            if _is_safe_word(w) or TOKEN_RE.search(w) and not TOKEN_RE.sub("", w).strip(".,:;()|-"):
                words_out.append(w)
            elif TOKEN_RE.search(w):  # token glued to unknown text: keep token, drop the rest
                words_out.extend(TOKEN_RE.findall(w) and [f"[[{a}_{b}]]" for a, b in TOKEN_RE.findall(w)])
                redacted += 1
            else:
                words_out.append(REDACTED)
                redacted += 1
        masked = " ".join(words_out)
        if any(li in indices for indices in holder_name_tokens.values()):
            # Carry the actual label association into the masked text so the
            # provider needn't infer a two-column layout from reading order.
            if not re.search(r"(?i)(?:employee\s+)?name\s*:", masked):
                label = "Employee name: " if category == "SALARY_SLIP" else "Name: "
                masked = label + masked.lstrip(" :;-|~().")
        if any(li in indices for indices in father_name_tokens.values()):
            if not re.search(r"(?i)(?:father'?s?\s+name|s/o|d/o)\s*:", masked):
                masked = "Father's name: " + masked
        if category in ("AADHAAR", "PAN"):
            masked = _DIGITS_RE.sub(REDACTED, masked)
            # Mask a whole numeric fragment when replacing only its long digit
            # sequence would leave e.g. 14/07/[REDACTED] in the payload.
            masked = re.sub(r"(?<!\S)[\d/.,:;()+\-]*\[REDACTED\][\d/.,:;()+\-]*(?!\S)", REDACTED, masked)
        masked = re.sub(r"(?:\[REDACTED\]\s*){2,}", REDACTED + " ", masked).strip()
        if masked:
            out_lines.append(SanitizedLine(li, line.page, line.bbox, masked))
    # A label or NER may identify a value in one location while another occurrence
    # survives vocabulary minimisation (e.g. a name containing ordinary words).
    # Redact every known textual value before the gate; never rewrite placeholders
    # or turn an unlabelled occurrence into new extraction evidence.
    originals = sorted({v.strip() for v in vault.originals()
                        if len(v.strip()) >= 4 and re.search(r"[a-z]", v, re.I)}, key=len, reverse=True)
    if originals:
        residual_values = re.compile("|".join(re.escape(v) for v in originals), re.I)
        for ln in out_lines:
            pieces, cursor, replacements = [], 0, 0
            for token in TOKEN_RE.finditer(ln.text):
                fragment, n = residual_values.subn(REDACTED, ln.text[cursor:token.start()])
                pieces.extend([fragment, token.group()])
                replacements += n
                cursor = token.end()
            fragment, n = residual_values.subn(REDACTED, ln.text[cursor:])
            pieces.append(fragment)
            replacements += n
            if replacements:
                ln.text = "".join(pieces)
                redacted += replacements
                boxes.append({"page": ln.page, "entity": "ID", "bbox": list(ln.bbox)})
    return Sanitized(out_lines, dict(counts), boxes, token_lines, redacted, holder_name_tokens, father_name_tokens)


# =================================================================== privacy gate
@dataclass
class GateResult:
    approved: bool
    reasons: list[str]


_RESIDUAL = [
    ("RESIDUAL_AADHAAR_LIKE", re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")),
    ("RESIDUAL_PAN_LIKE", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])")),
    ("RESIDUAL_EMAIL", re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")),
    ("RESIDUAL_PHONE", re.compile(r"(?<!\d)(?:\+91[\-\s]?)?[6-9]\d{4}\s?\d{5}(?!\d)")),
    ("RESIDUAL_LONG_NUMBER", re.compile(r"(?<![\d,.])\d{9,}(?![\d,.])")),
    ("RESIDUAL_IFSC", re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")),
]


def privacy_gate(sanitized: Sanitized, category: str, vault: TokenVault, payload: str) -> GateResult:
    """Document-specific approval of the outbound payload. Blocks on any residual signal."""
    reasons: list[str] = []
    body = re.sub(r"^L\d{3} \[p\d+\]: ", "", payload, flags=re.M)
    body_no_tokens = TOKEN_RE.sub(" ", body)
    for code, rx in _RESIDUAL:
        if rx.search(body_no_tokens):
            reasons.append(code)
    lowered = body_no_tokens.casefold()
    body_digits = re.sub(r"\D", "", body_no_tokens)
    for original in vault.originals():
        o = original.casefold().strip()
        if len(o) >= 4 and re.search(r"[a-z]", o) and o in lowered:
            reasons.append("VAULT_VALUE_PRESENT")
            break
        digits = re.sub(r"\D", "", o)
        if len(digits) >= 8 and digits in body_digits:
            reasons.append("VAULT_DIGITS_PRESENT")
            break
    for ln in sanitized.lines:
        for m in _LABEL_WITH_SEP.finditer(ln.text):
            if _label_type(m) in SENSITIVE_LABEL_TYPES:
                rest = ln.text[m.end():].split()
                head = rest[0].strip(",.;") if rest else ""
                if head and not (TOKEN_RE.fullmatch(head) or head.startswith(REDACTED) or _LABEL_WITH_SEP.match(
                        " ".join(rest))):
                    reasons.append("UNMASKED_LABELLED_FIELD")
    for w in body.split():
        if not _is_safe_word(w):
            reasons.append("UNAPPROVED_FREE_TEXT")
            break
    if not sanitized.lines:
        reasons.append("EMPTY_PAYLOAD")
    return GateResult(not reasons, sorted(set(reasons)))
