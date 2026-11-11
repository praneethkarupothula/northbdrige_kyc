"""Write synthetic text-layer PDFs for one fictional applicant (unique identifiers per seed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import synthetic as sy  # noqa: E402

seed, out = int(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
p = sy.person(5, seed=seed)
files = {
    "aadhaar.pdf": sy.aadhaar_doc(p), "pan.pdf": sy.pan_doc(p),
    "salary_march.pdf": sy.salary_doc(p, sy.SalaryInfo(month="March")),
    "salary_april.pdf": sy.salary_doc(p, sy.SalaryInfo(month="April")),
}
for name, d in files.items():
    (out / name).write_bytes(sy.to_pdf_text(d))
print(json.dumps({"name": p.name, "dir": str(out)}))
