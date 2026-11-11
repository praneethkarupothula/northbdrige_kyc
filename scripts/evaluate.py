"""Evaluate extraction + rules on the SYNTHETIC sample set (samples/manifest.json).

  uv run python ../scripts/evaluate.py                 # scripted test double (APP_ENV=test), no network
  APP_ENV=development LLM_PROVIDER=gemini GEMINI_API_KEY=... uv run python ../scripts/evaluate.py --provider gemini

Reports field accuracy, document-type accuracy and rule accuracy with explicit denominators.
These numbers describe synthetic template documents only - NOT real-world performance.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ap = argparse.ArgumentParser()
ap.add_argument("--provider", default="scripted", choices=["scripted", "gemini"])
ap.add_argument("--set", default="mixed", choices=["mixed", "text_pdf"],
                help="mixed = PDF/PNG/JPEG/scanned PDF as listed; text_pdf = same documents as text-layer PDFs")
ap.add_argument("--out", default=None)
args = ap.parse_args()
if args.provider == "scripted":
    os.environ.setdefault("APP_ENV", "test")
    os.environ["LLM_PROVIDER"] = "scripted"
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("IDENTITY_HMAC_SECRET", "eval-" + "x" * 40)

from app.pipeline import identifiers as ids  # noqa: E402
from app.pipeline.llm import get_provider  # noqa: E402
from app.pipeline.matching import DocOutcome, evaluate  # noqa: E402
from app.pipeline.ocr import OcrUnavailable, extract_text  # noqa: E402
from app.pipeline.output_validation import OutputRejected, validate_output  # noqa: E402
from app.pipeline.pii import TokenVault, privacy_gate, sanitize  # noqa: E402

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def main() -> None:
    manifest = json.loads((ROOT / "samples" / "manifest.json").read_text())["documents"]
    provider = get_provider()
    field_hits, field_total = defaultdict(int), defaultdict(int)
    type_hits = type_total = 0
    skipped: list[dict] = []
    per_app: dict[int, list] = defaultdict(list)
    vaults: dict[int, TokenVault] = {}
    for row in manifest:
        if args.set == "text_pdf":
            data = (ROOT / "samples" / "text_pdf" / (row["file"].rsplit(".", 1)[0] + ".pdf")).read_bytes()
            fmt = "pdf"
        else:
            data = (ROOT / "samples" / row["file"]).read_bytes()
            fmt = "pdf" if row["format"] in ("pdf", "scanned_pdf") else row["format"]
        vault = vaults.setdefault(row["applicant_index"], TokenVault(f"eval-{row['applicant_index']}"))
        try:
            text = extract_text(data, fmt, first_page_only=row["category"] == "BANK_STATEMENT")
        except OcrUnavailable:
            skipped.append({"file": row["file"], "reason": "OCR_UNAVAILABLE (Tesseract not installed)"})
            per_app[row["applicant_index"]].append((row, None))
            continue
        san = sanitize(text.lines, row["category"], vault)
        payload = san.payload(12000)
        gate = privacy_gate(san, row["category"], vault, payload)
        if not text.readable or not gate.approved:
            reason = "UNREADABLE" if not text.readable else "PRIVACY_GATE_BLOCKED"
            skipped.append({"file": row["file"], "reason": reason})
            per_app[row["applicant_index"]].append((row, None))
            continue
        try:
            val = validate_output(provider.extract(payload, row["category"]).extraction, san, vault, payload)
        except OutputRejected as e:
            skipped.append({"file": row["file"], "reason": f"OUTPUT_REJECTED:{e.reason}"})
            per_app[row["applicant_index"]].append((row, None))
            continue
        truth = row["truth"]
        type_total += 1
        type_hits += val.document_type == truth["document_type"]
        got = {f: (vault.original(t) if t else None) for f, t in val.tokens.items()}
        for f in ("full_name", "date_of_birth", "id_number", "pan_number", "employer_name"):
            if f in truth:
                field_total[f] += 1
                g, t = got.get(f), truth[f]
                if f == "full_name" or f == "employer_name":
                    ok = g is not None and ids.normalize_name(g) == ids.normalize_name(t)
                else:
                    ok = g is not None and "".join(g.split()).upper() == "".join(t.split()).upper()
                field_hits[f] += ok
        for f in ("gross_salary", "net_salary"):
            if f in truth:
                field_total[f] += 1
                field_hits[f] += str(val.money.get(f)) == truth[f]
        if "pay_period" in truth:
            field_total["pay_period"] += 1
            m, y = truth["pay_period"]
            field_hits["pay_period"] += (val.pay_period_month, val.pay_period_year) == (MONTHS.index(m) + 1, y)
        name = got.get("full_name")
        per_app[row["applicant_index"]].append((row, DocOutcome(
            row["file"], row["category"], usable=True, document_type=val.document_type,
            name_token=val.tokens.get("full_name"), name_norm=ids.normalize_name(name) if name else None,
            pan_token=val.tokens.get("pan_number") if row["category"] == "SALARY_SLIP"
                else val.tokens.get("id_number") if row["category"] == "PAN" else None)))
    rule_hits = rule_total = 0
    rule_rows = []
    for _idx, items in per_app.items():
        outcomes = [o for _, o in items if o is not None]
        if any(o is None for _, o in items):
            continue  # a document could not be evaluated -> rules for this applicant not scored
        checks, agg = evaluate(outcomes, pan_status="SUPPLIED", pan_policy="required", required_salary_slips=1,
                               account_name_norm=None)
        for row, o in items:
            if row["category"] not in ("SALARY_SLIP", "PAN"):
                continue
            c = next(c for c in checks if c.document_id == o.document_id and c.check_type.startswith("NAME_"))
            rule_total += 1
            rule_hits += c.status == row["expected_name_check"]
            rule_rows.append({"file": row["file"], "expected": row["expected_name_check"], "got": c.status})
    for v in vaults.values():
        v.clear()
    result = {
        "dataset": f"samples/manifest.json, set={args.set} (synthetic template documents)",
        "documents_in_dataset": len(manifest),
        "documents_evaluated": type_total,
        "documents_not_evaluated": skipped,
        "provider": provider.name,
        "document_type_accuracy": {"correct": type_hits, "total": type_total},
        "field_accuracy": {f: {"correct": field_hits[f], "total": field_total[f]} for f in sorted(field_total)},
        "rule_accuracy": {"correct": rule_hits, "total": rule_total, "cases": rule_rows},
        "caveat": "Synthetic, template-generated documents only. Not representative of real-world scans.",
    }
    Path(args.out or ROOT / "docs" / f"EVALUATION_RESULTS_{args.set}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ("documents_not_evaluated",)}, indent=2)[:3000])
    print(f"not evaluated: {len(skipped)} -> " + ", ".join(sorted({s['reason'] for s in skipped})))


main()
