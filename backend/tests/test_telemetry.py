from app.observability.telemetry import allowlist_processor, filter_attributes


def test_allowlist_drops_unknown_and_invalid_fields():
    out = allowlist_processor(None, "info", {
        "event": "x_done", "application_id": "8b0c1f6e-1111-4c4c-8888-0123456789ab", "full_name": "Asha Verma",
        "stage": "PII_MASKING", "text": "Name: Asha Verma", "status": "Asha Verma has spaces",
        "error_class": "ValueError", "filename": "aadhaar_asha.pdf", "duration_ms": 12,
    })
    assert out["application_id"] and out["stage"] == "PII_MASKING" and out["duration_ms"] == 12
    for k in ("full_name", "text", "status", "filename"):
        assert k not in out
    assert out["dropped_fields"] == 4
    assert allowlist_processor(None, "info", {"event": "Asha Verma logged in"})["event"] == "unlabelled_event"


def test_span_attributes_filtered():
    assert filter_attributes({"document_id": "nope", "stage": "OCR", "email": "a@b.c"}) == {"stage": "OCR"}


def test_validation_errors_do_not_echo_values(make_client):
    api = make_client()
    r = api.post("/v1/auth/register", json={"full_name": "A", "email": "not-an-email", "password": "Secret-Value-123",
                                            "password_confirm": "x"})
    assert r.status_code == 422 and "Secret-Value-123" not in r.text and "not-an-email" not in r.text
