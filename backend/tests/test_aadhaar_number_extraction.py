import pytest

from app.pipeline.identifiers import aadhaar_plausible, canonical_aadhaar
from app.pipeline.llm import LlmExtraction
from app.pipeline.ocr import Line, Word
from app.pipeline.output_validation import validate_output
from app.pipeline.pii import TokenVault, privacy_gate, sanitize


@pytest.mark.parametrize("printed", ["1211 4001 2003", "0000 0000 0000", "121140012003", "1211-4001-2003"])
def test_printed_sample_number_is_extracted_without_bypassing_validity(printed):
    line = Line(1, [Word(w, (0.1, 0.2, 0.5, 0.3), None) for w in printed.split()], "tesseract")
    with TokenVault("sample-id") as vault:
        san = sanitize([line], "AADHAAR", vault, use_presidio=False)
        token = next(t for t in san.token_lines if vault.type_of(t) == "AADHAAR")
        payload = san.payload(12000)
        assert printed not in payload
        assert privacy_gate(san, "AADHAAR", vault, payload).approved
        result = validate_output(LlmExtraction(document_type="AADHAAR", id_number=token, id_number_line=0),
                                 san, vault, payload)
        exact = vault.original(result.tokens["id_number"])
        assert canonical_aadhaar(exact) == canonical_aadhaar(printed)
        assert aadhaar_plausible(canonical_aadhaar(exact))[0] is False
