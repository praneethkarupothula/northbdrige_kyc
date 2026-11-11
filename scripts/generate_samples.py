"""Write the SYNTHETIC sample/evaluation documents to samples/ with a ground-truth manifest.

Run from backend/:  uv run python ../scripts/generate_samples.py
Every file is fictional and carries the banner "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import synthetic as sy  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "samples"


def build() -> list[dict]:
    OUT.mkdir(exist_ok=True)
    rows = []
    fmts = ["pdf", "png", "jpeg", "scanned_pdf"]
    for i in range(6):
        p = sy.person(i)
        docs = [sy.aadhaar_doc(p), sy.pan_doc(p), sy.salary_doc(p, sy.SalaryInfo(month=["January", "February", "March", "April", "May", "June"][i])), sy.bank_doc(p)]
        if i == 4:  # deliberate mismatch case for rule accuracy
            docs[2] = sy.salary_doc(p, sy.SalaryInfo(month="May"), name_override="Priya Deshmukh")
        for d in docs:
            fmt = fmts[(i + len(rows)) % len(fmts)]
            ext = {"pdf": "pdf", "scanned_pdf": "pdf", "png": "png", "jpeg": "jpg"}[fmt]
            name = f"synthetic_{p.name.split()[0].lower()}_{d.category.lower()}_{fmt}.{ext}"
            (OUT / name).write_bytes(sy.RENDERERS[fmt](d))
            (OUT / "text_pdf").mkdir(exist_ok=True)
            (OUT / "text_pdf" / (name.rsplit(".", 1)[0] + ".pdf")).write_bytes(sy.to_pdf_text(d))
            truth = {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.truth.items()}
            rows.append({"file": name, "category": d.category, "format": fmt, "applicant_index": i, "truth": truth,
                         "expected_name_check": "MISMATCH" if (i == 4 and d.category == "SALARY_SLIP") else "MATCH"})
    (OUT / "manifest.json").write_text(json.dumps({
        "notice": "All documents are synthetic test fixtures. People, employers, banks and identifiers are fictional.",
        "documents": rows}, indent=2))
    return rows


if __name__ == "__main__":
    print(f"wrote {len(build())} synthetic documents to {OUT}")
