"""Acceptance 12: oversized, spoofed, corrupt and encrypted files; upload limits."""

import io

import pymupdf
from PIL import Image

from app import synthetic as sy
from tests.helpers import start, upload


def _code(r):
    return r.json()["detail"]["code"]


def test_rejections(applicant):
    api = applicant()
    aid = start(api)["id"]
    big = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024 + 10)
    r = upload(api, aid, "AADHAAR", big)
    assert r.status_code in (413, 422)
    r = upload(api, aid, "AADHAAR", b"MZ\x90\x00 this is an exe", "pdf", filename="aadhaar.pdf")
    assert r.status_code == 422 and _code(r) == "UNSUPPORTED_TYPE"
    r = upload(api, aid, "AADHAAR", b"just text pretending to be png", "png", filename="a.png")
    assert _code(r) == "UNSUPPORTED_TYPE"
    png = sy.to_png(sy.aadhaar_doc(sy.person(0)))
    r = upload(api, aid, "AADHAAR", png[: len(png) // 2], "png")
    assert _code(r) == "CORRUPT_FILE"
    r = upload(api, aid, "AADHAAR", b"%PDF-1.7\n1 0 obj << /Type /Catalog >> garbage", "pdf")
    assert _code(r) == "CORRUPT_FILE"
    r = upload(api, aid, "AADHAAR", sy.encrypted_pdf(), "pdf")
    assert _code(r) == "ENCRYPTED_PDF"
    doc = pymupdf.open()
    for _ in range(11):
        doc.new_page()
    r = upload(api, aid, "SALARY_SLIP", doc.tobytes(), "pdf")
    assert _code(r) == "TOO_MANY_PAGES"
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, "PNG")
    assert _code(upload(api, aid, "AADHAAR", buf.getvalue(), "png")) == "IMAGE_TOO_SMALL"
    # client-declared JPEG that is really a PNG: accepted *as PNG* because content decides
    r = upload(api, aid, "AADHAAR", png, "jpeg", filename="photo.jpg")
    assert r.status_code == 201
    app = api.get(f"/v1/applications/{aid}").json()
    assert app["current"]["documents"][0]["file_format"] == "png"


def test_pixel_bomb_rejected(applicant, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("MAX_IMAGE_PIXELS", "1000000")
    get_settings.cache_clear()
    api = applicant()
    aid = start(api)["id"]
    buf = io.BytesIO()
    Image.new("L", (2000, 2000), 255).save(buf, "PNG")
    assert _code(upload(api, aid, "AADHAAR", buf.getvalue(), "png")) == "IMAGE_TOO_LARGE"


def test_limits_and_pan_choice(applicant):
    api = applicant()
    aid = start(api)["id"]
    p = sy.person(0)
    for m in ["January", "February", "March", "April", "May", "June"]:
        assert upload(api, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p, sy.SalaryInfo(month=m)))).status_code == 201
    assert upload(api, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p))).status_code == 409
    assert upload(api, aid, "AADHAAR", sy.to_pdf_text(sy.aadhaar_doc(p))).status_code == 201
    assert upload(api, aid, "AADHAAR", sy.to_pdf_text(sy.aadhaar_doc(p))).status_code == 409
    cur = api.get(f"/v1/applications/{aid}").json()["current"]
    r = api.post(f"/v1/applications/{aid}/submit", json={"consent": True, "row_version": cur["row_version"]})
    assert r.status_code == 422 and "PAN" in r.json()["detail"]["message"], "PAN upload is mandatory"
    assert api.patch(f"/v1/applications/{aid}/draft", json={"pan_choice": "UNAVAILABLE"}).status_code == 422
    assert upload(api, aid, "PAN", sy.to_pdf_text(sy.pan_doc(p))).status_code == 201
    cur = api.get(f"/v1/applications/{aid}").json()["current"]
    r = api.post(f"/v1/applications/{aid}/submit", json={"consent": False, "row_version": cur["row_version"]})
    assert r.status_code == 422
    r = api.post(f"/v1/applications/{aid}/submit", json={"consent": True, "row_version": cur["row_version"] - 1})
    assert r.status_code == 409


def test_admin_policy_changes_requirements(applicant, staff):
    adm = staff("admin")
    pol = adm.get("/v1/admin/policy").json()
    assert pol["pan_policy"] == "required"
    r = adm.put("/v1/admin/policy", json={**{k: pol[k] for k in pol if k != "notice"}, "pan_policy": "required",
                                          "max_salary_slips": 3})
    assert r.status_code == 200
    api = applicant()
    aid = start(api)["id"]
    assert api.patch(f"/v1/applications/{aid}/draft", json={"pan_choice": "UNAVAILABLE"}).status_code == 422
    p = sy.person(0)
    for m in ["January", "February", "March"]:
        upload(api, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p, sy.SalaryInfo(month=m))))
    assert upload(api, aid, "SALARY_SLIP", sy.to_pdf_text(sy.salary_doc(p))).status_code == 409
    assert adm.get("/v1/admin/metrics").status_code == 200


def test_quality_flags_on_clean_and_degraded_pages():
    import numpy as np

    from app.pipeline.ocr import inspect_document
    from app.pipeline.quality import inspect

    assert inspect_document(sy.to_pdf_text(sy.aadhaar_doc(sy.person(0))), "pdf")["flags"] == []
    clean = inspect_document(sy.to_png(sy.salary_doc(sy.person(0))), "png")
    assert "TOO_BRIGHT" not in clean["flags"] and clean["pages"][0]["dpi_source"] == "metadata"
    washed = np.full((800, 800), 250, dtype=np.uint8)
    washed[400:410, 100:700] = 235
    assert "TOO_BRIGHT" in inspect(washed, dpi=None, dpi_source="missing")["flags"]
    dark = np.full((800, 800), 30, dtype=np.uint8)
    assert "TOO_DARK" in inspect(dark, dpi=None, dpi_source="missing")["flags"]
    blurry = inspect_document(sy.to_png(sy.aadhaar_doc(sy.person(0)), blur=6), "png")
    assert "BLURRY" in blurry["flags"] and blurry["suggestions"]
