from __future__ import annotations

from app import synthetic as sy

MIME = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg", "scanned_pdf": "application/pdf"}
EXT = {"pdf": "pdf", "png": "png", "jpeg": "jpg", "scanned_pdf": "pdf"}


def start(api) -> dict:
    r = api.post("/v1/applications")
    assert r.status_code == 200, r.text
    return r.json()


def upload(api, app_id: str, category: str, data: bytes, fmt: str = "pdf", *, filename=None, replace=None):
    files = {"file": (filename or f"upload.{EXT[fmt]}", data, MIME[fmt])}
    form = {"category": category}
    if replace:
        form["replace_document_id"] = replace
    return api.post(f"/v1/applications/{app_id}/documents", data=form, files=files)


def upload_doc(api, app_id: str, doc: sy.SynthDoc, fmt: str = "pdf"):
    r = upload(api, app_id, doc.category, sy.RENDERERS[fmt](doc), fmt)
    assert r.status_code == 201, r.text
    return r.json()


def submit(api, app_id: str) -> dict:
    cur = api.get(f"/v1/applications/{app_id}").json()["current"]
    r = api.post(f"/v1/applications/{app_id}/submit", json={"consent": True, "row_version": cur["row_version"]})
    assert r.status_code == 200, r.text
    return r.json()


def supply_pan(api, app_id: str, person=None):
    upload_doc(api, app_id, sy.pan_doc(person or sy.person(0)))


def current(api, app_id: str) -> dict:
    return api.get(f"/v1/applications/{app_id}").json()["current"]


def full_flow(api, worker, person, *, pan=True, slips=None, fmt="pdf", bank=False):
    from app.workers.runner import drain

    app = start(api)
    aid = app["id"]
    upload_doc(api, aid, sy.aadhaar_doc(person), fmt)
    if pan:
        upload_doc(api, aid, sy.pan_doc(person), fmt)
    for s in slips or [sy.salary_doc(person)]:
        upload_doc(api, aid, s, fmt)
    if bank:
        upload_doc(api, aid, sy.bank_doc(person), fmt)
    submit(api, aid)
    drain(worker)
    return aid, current(api, aid)


def checks_by_type(cur: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in cur["checks"]:
        out.setdefault(c["check_type"], []).append(c)
    return out
