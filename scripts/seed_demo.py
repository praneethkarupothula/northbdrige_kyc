"""Seed a DEMO applicant (processed, awaiting review) and a demo admin into the E2E/test stack.

Synthetic data only. Run with the API on :8000:  bash -c 'source scripts/e2e-env.sh && cd backend && uv run python ../scripts/seed_demo.py'
Demo credentials (test values for the local test stack only):
  applicant: demo.applicant@example.com / Demo-Applicant-2026!
  admin:     demo.admin@northbridge.example.com / Review-Desk-2026!
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import synthetic as sy  # noqa: E402

API = os.environ.get("NB_API", "http://127.0.0.1:8000")
APPLICANT = ("demo.applicant@example.com", "Demo-Applicant-2026!")
ADMIN = ("demo.admin@northbridge.example.com", "Review-Desk-2026!")

env = dict(os.environ, NB_BOOTSTRAP_PASSWORD=ADMIN[1])
subprocess.run([sys.executable, "-m", "app.cli", "bootstrap-admin", "--email", ADMIN[0], "--name", "Demo Admin"], env=env)

c = httpx.Client(base_url=API)
c.get("/v1/auth/csrf")
h = lambda: {"X-CSRF-Token": c.cookies.get("nb_csrf")}  # noqa: E731
p = sy.person(3, seed=int(time.time()) % 9999)
r = c.post("/v1/auth/register", headers=h(), json={"full_name": p.name, "email": APPLICANT[0], "password": APPLICANT[1], "password_confirm": APPLICANT[1]})
if r.status_code == 409:
    print("demo applicant already exists")
    sys.exit(0)
app = c.post("/v1/applications", headers=h()).json()
docs = [sy.aadhaar_doc(p), sy.pan_doc(p), sy.salary_doc(p, sy.SalaryInfo(month="March")),
        sy.salary_doc(p, sy.SalaryInfo(month="April", basic=31000)), sy.bank_doc(p)]
for d in docs:
    c.post(f"/v1/applications/{app['id']}/documents", headers=h(), data={"category": d.category},
           files={"file": ("x.pdf", sy.to_pdf_text(d), "application/pdf")}).raise_for_status()
cur = c.get(f"/v1/applications/{app['id']}").json()["current"]
c.post(f"/v1/applications/{app['id']}/submit", headers=h(), json={"consent": True, "row_version": cur["row_version"]}).raise_for_status()
print("seeded", app["reference"])
