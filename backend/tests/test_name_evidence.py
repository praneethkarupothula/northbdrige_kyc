import pytest

from app.pipeline.llm import LlmExtraction
from app.pipeline.ocr import Line, Word, split_text_columns
from app.pipeline.output_validation import OutputRejected, validate_output
from app.pipeline.pii import Sanitized, SanitizedLine, TokenVault, sanitize


def test_pan_name_line_does_not_include_distant_qr_text():
    words = [Word("RAVI", (0.07, 0.58, 0.17, 0.62), 95),
             Word("MENON", (0.19, 0.58, 0.32, 0.62), 96),
             Word("ofS", (0.8, 0.58, 0.86, 0.67), 28)]
    lines = split_text_columns(words, 1, "tesseract")
    assert [line.text for line in lines] == ["RAVI MENON", "ofS"]
    with TokenVault("qr") as vault:
        san = sanitize([Line(1, [Word("ABCPV1234K", (0.3, 0.4, 0.6, 0.45), 95)], "tesseract")] + lines, "PAN", vault, use_presidio=False)
        assert any(vault.original(t) == "RAVI MENON" for t in san.token_lines)
        assert all(vault.original(t) != "RAVI MENON ofS" for t in san.token_lines)


def test_pan_number_under_name_label_is_not_tokenised_as_person():
    line = Line(1, [Word(w, (0.1, 0.1, 0.3, 0.15), None) for w in "Name: ABCPV1234K".split()], "pdf_text")
    with TokenVault("wrong-label") as vault:
        san = sanitize([line], "PAN", vault, use_presidio=False)
        assert not san.holder_name_tokens
        assert "[[PERSON_" not in san.payload(12000)


def test_validator_rejects_identifier_even_if_mistokenised_as_person():
    with TokenVault("guard") as vault:
        token = vault.token_for("PERSON", "ABCPV1234K")
        san = Sanitized([SanitizedLine(0, 1, (0, 0, 1, 1), token)], {}, [])
        ex = LlmExtraction(document_type="PAN", full_name=token, full_name_line=0)
        with pytest.raises(OutputRejected, match="INVALID_PERSON_NAME"):
            validate_output(ex, san, vault, san.payload(12000))


def test_bilingual_pan_name_label_excludes_heading_and_signature():
    def line(text, y):
        return Line(1, [Word(w, (0.05, y, 0.3, y + 0.025), 95) for w in text.split()], "tesseract")

    lines = [line("Stree Taant", 0.08), line("INCOME TAX DEPARTMENT", 0.18),
             line("FGHIJ2345K", 0.4), line("aTa / Name", 0.56), line("MEERA IYER", 0.61),
             line("fat | Father's Name", 0.68), line("APPLICANT FATHER NAME", 0.74),
             line("Signature Text", 0.9)]
    with TokenVault("bilingual") as vault:
        san = sanitize(lines, "PAN", vault, use_presidio=False)
        assert len(san.holder_name_tokens) == 1
        token = next(iter(san.holder_name_tokens))
        assert vault.original(token) == "MEERA IYER"
        good = LlmExtraction(document_type="PAN", full_name=token, full_name_line=4)
        assert validate_output(good, san, vault, san.payload(12000)).tokens["full_name"] == token


def test_aadhaar_preserves_surname_before_unlabelled_dob():
    def line(text, y):
        return Line(1, [Word(w, (0.35, y, 0.65, y + 0.04), 95) for w in text.split()], "tesseract")

    with TokenVault("surname") as vault:
        san = sanitize([line("GOVERNMENT OF INDIA", 0.2), line("Meera lyer", 0.35),
                        line("Female", 0.46), line("09/02/1990", 0.55)], "AADHAAR", vault, use_presidio=False)
        token = next(iter(san.holder_name_tokens))
        assert vault.original(token) == "Meera lyer"
        assert san.holder_name_tokens[token] == [1]


def test_ocr_confusable_names_need_review_not_a_false_mismatch():
    from app.pipeline.matching import DocOutcome, _pair

    card = DocOutcome("a", "AADHAAR", True, name_token="[[PERSON_1]]", name_norm="meera lyer", name_from_ocr=True)
    slip = DocOutcome("s", "SALARY_SLIP", True, name_token="[[PERSON_2]]", name_norm="meera iyer")
    result = _pair("NAME_SALARY_VS_AADHAAR", slip, card)
    assert result.status == "INCOMPLETE" and result.code == "NAME_OCR_UNCERTAIN"
    card.name_from_ocr = False
    assert _pair("NAME_SALARY_VS_AADHAAR", slip, card).status == "MISMATCH"


def test_aadhaar_name_survives_portrait_graphic_noise():
    def line(text, y):
        return Line(1, [Word(w, (0.3, y, 0.7, y + 0.04), 94) for w in text.split()], "tesseract")

    lines = [line("GOVERNMENT OF INDIA", 0.2), line("| ~~.) LEELA SHAH", 0.36),
             line("(==) Female", 0.46), line("= J 13/06/1994", 0.55), line("1316 9556 7558", 0.68)]
    with TokenVault("portrait-noise") as vault:
        san = sanitize(lines, "AADHAAR", vault, use_presidio=False)
        token = next(iter(san.holder_name_tokens))
        assert vault.original(token) == "LEELA SHAH"
        assert f"Name: {token}" in san.payload(12000)
        result = validate_output(LlmExtraction(document_type="AADHAAR", full_name=token, full_name_line=1),
                                 san, vault, san.payload(12000))
        assert result.tokens["full_name"] == token
        assert "LEELA" not in san.payload(12000)


def test_old_pan_name_before_number_with_shuffled_pdf_order():
    def line(text, y):
        return Line(1, [Word(w, (0.08, y, 0.45, y + 0.025), 95) for w in text.split()], "pdf_text")
    lines = [line("ABCDE1234F", 0.75), line("Signature Text", 0.9),
             line("INCOME TAX DEPARTMENT", 0.12), line("MEERA IYER", 0.3),
             line("RAJESH IYER", 0.42), line("09/02/1990", 0.55)]
    with TokenVault("old-pan") as vault:
        san = sanitize(lines, "PAN", vault, use_presidio=False)
        assert len(san.holder_name_tokens) == 1
        assert vault.original(next(iter(san.holder_name_tokens))) == "MEERA IYER"


def test_pan_label_last_in_pdf_order_with_indented_value():
    def line(text, x, y):
        return Line(1, [Word(w, (x, y, x + 0.3, y + 0.02), 95) for w in text.split()], "pdf_text")
    lines = [line("MEERA IYER", 0.15, 0.4), line("Father's Name", 0.08, 0.55),
             line("RAJESH IYER", 0.15, 0.62), line("Signature Text", 0.1, 0.85), line("Name", 0.08, 0.3)]
    with TokenVault("pan-label-gap") as vault:
        san = sanitize(lines, "PAN", vault, use_presidio=False)
        assert len(san.holder_name_tokens) == 1
        assert vault.original(next(iter(san.holder_name_tokens))) == "MEERA IYER"


@pytest.mark.parametrize("dob", ["14/07/1895", "14/07/1995", "1895-07-14"])
def test_pan_old_birth_year_is_masked_without_blocking_holder(dob):
    from app.pipeline.pii import privacy_gate
    def line(text, y):
        return Line(1, [Word(w, (0.05, y, 0.5, y + 0.025), 95) for w in text.split()], "tesseract")
    lines = [line("INCOME TAX DEPARTMENT GOVT. OF INDIA", 0.1),
             line("Permanent Account Number Card", 0.3), line("KLMNO3345M", 0.4),
             line("Name", 0.5), line("DEV PATEL", 0.54),
             line("Father's Name", 0.6), line("Date of Birth", 0.7), line(dob, 0.74),
             line("Signature", 0.85)]
    with TokenVault("pan-old-date") as vault:
        san = sanitize(lines, "PAN", vault, use_presidio=False)
        payload = san.payload(12000)
        assert privacy_gate(san, "PAN", vault, payload).approved
        assert dob not in payload and "DEV PATEL" not in payload and "KLMNO3345M" not in payload
        token = next(iter(san.holder_name_tokens))
        assert vault.original(token) == "DEV PATEL"
        result = validate_output(LlmExtraction(document_type="PAN", full_name=token, full_name_line=4), san, vault, payload)
        assert result.tokens["full_name"] == token
