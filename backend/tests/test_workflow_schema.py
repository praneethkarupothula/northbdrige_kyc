import pytest
from pydantic import ValidationError

from app import synthetic as sy
from app.pipeline.ocr import Line, PageText, TextResult, Word
from app.pipeline.ocr_schema import OcrResultSchema
from tests.helpers import full_flow


def test_ocr_schema_checks_bounds_and_page_references():
    word = Word("Private Person", (0.1, 0.2, 0.5, 0.3), 95)
    line = Line(1, [word], "tesseract")
    result = TextResult([PageText(1, "tesseract", [line], 95, 13)], 1)
    assert OcrResultSchema.model_validate(result).pages[0].lines[0].words[0].conf == 95
    word.bbox = (0.6, 0.2, 0.5, 0.3)
    with pytest.raises(ValidationError):
        OcrResultSchema.model_validate(result)
    word.bbox = (0.1, 0.2, 0.5, 0.3)
    line.page = 2
    with pytest.raises(ValidationError):
        OcrResultSchema.model_validate(result)


def test_new_workflow_stages_are_real_and_privacy_safe(applicant, worker):
    api = applicant()
    aid, v = full_flow(api, worker, sy.person(0))
    events = api.get(f"/v1/applications/{aid}/events").json()["events"]
    for doc in v["documents"]:
        evs = [e for e in events if e["document_id"] == doc["id"]]
        completed = [e["stage"] for e in evs if e["status"] == "succeeded"]
        assert completed.index("TEXT_EXTRACTION") < completed.index("OCR_SCHEMA_VALIDATION") < completed.index("PII_MASKING")
        assert completed.index("LLM_EXTRACTION") < completed.index("LLM_TELEMETRY") < completed.index("SCHEMA_VALIDATION")
        msg = next(e["message"] for e in evs if e["stage"] == "LLM_TELEMETRY" and e["status"] == "succeeded")
        assert "input tokens=" in msg and "latency=" in msg
        assert sy.person(0).name not in msg and sy.person(0).pan not in msg


def test_schema_definition_is_staff_only(applicant, staff):
    assert applicant().get("/v1/staff/processing-schema").status_code == 403
    response = staff("admin").get("/v1/staff/processing-schema")
    assert response.status_code == 200
    schema = response.json()
    assert schema["title"] == "OcrResultSchema"
    assert "OcrWordSchema" in schema["$defs"]
    assert schema["$defs"]["OcrWordSchema"]["properties"]["conf"]
