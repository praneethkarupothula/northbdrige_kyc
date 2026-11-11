"""Acceptance 10-11: PDF, JPG/JPEG and PNG for every document category; scanned PDFs use Tesseract.

Requires the Tesseract binary (skipped otherwise)."""

import pytest

from app import synthetic as sy
from tests.conftest import requires_ocr
from tests.helpers import checks_by_type, full_flow

pytestmark = [requires_ocr, pytest.mark.ocr]


@pytest.mark.parametrize("fmt", ["pdf", "png", "jpeg", "scanned_pdf"])
def test_every_category_in_format(applicant, worker, fmt, monkeypatch):
    import pytesseract

    calls = []
    real = pytesseract.image_to_data
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: calls.append(1) or real(*a, **k))
    p = sy.person(0)
    _, cur = full_flow(applicant(), worker, p, pan=True, bank=True, fmt=fmt)
    docs = {d["category"]: d for d in cur["documents"]}
    assert set(docs) == {"AADHAAR", "PAN", "SALARY_SLIP", "BANK_STATEMENT"}
    assert all(d["processing_status"] == "SUCCEEDED" for d in docs.values()), [
        (d["category"], d["error_code"]) for d in docs.values()]
    assert cur["check_status"] == "MATCH", cur["checks"]
    c = checks_by_type(cur)
    assert c["NAME_PAN_VS_AADHAAR"][0]["status"] == "MATCH"
    method = docs["AADHAAR"]["extraction"]["extraction_method"]
    if fmt == "pdf":
        assert method == "pdf_text" and not calls, "embedded text must not invoke OCR"
        assert docs["AADHAAR"]["extraction"]["ocr_confidence"] is None, "no invented confidence"
    else:
        assert method.startswith("tesseract") and calls, "image / scanned input must use Tesseract"
        assert docs["AADHAAR"]["extraction"]["ocr_confidence"] is not None
    assert docs["SALARY_SLIP"]["extraction"]["net_salary"] == "45250.00"


def test_unreadable_image_needs_reupload(applicant, worker):
    from app.workers.runner import drain
    from tests.helpers import current, supply_pan, start, submit, upload

    p = sy.person(0)
    api = applicant()
    aid = start(api)["id"]
    blurry = sy.to_png(sy.aadhaar_doc(p), blur=9)
    assert upload(api, aid, "AADHAAR", blurry, "png").status_code == 201
    supply_pan(api, aid)
    upload(api, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p)))
    submit(api, aid)
    drain(worker)
    cur = current(api, aid)
    a = next(d for d in cur["documents"] if d["category"] == "AADHAAR")
    assert a["processing_status"] == "NEEDS_REUPLOAD" and a["error_code"] == "UNREADABLE"
    assert "BLURRY" in a["quality_flags"] and a["quality_suggestions"]
    assert cur["processing_status"] == "NEEDS_REUPLOAD" and cur["check_status"] == "INCOMPLETE"
    assert cur["status"]["key"] == "NEEDS_REUPLOAD"


def test_skewed_and_exif_rotated_image(applicant, worker):
    import io

    from PIL import Image

    p = sy.person(0)
    img = sy.to_image(sy.aadhaar_doc(p), rotate=4)
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 1
    img.save(buf, "JPEG", quality=92, exif=exif)
    from app.pipeline.ocr import extract_text, inspect_document

    q = inspect_document(buf.getvalue(), "jpeg")
    assert "SKEWED" in q["flags"] and q["pages"][0]["dpi_source"] == "missing"
    text = extract_text(buf.getvalue(), "jpeg")
    assert "Name:" in "\n".join(ln.text for ln in text.lines)
