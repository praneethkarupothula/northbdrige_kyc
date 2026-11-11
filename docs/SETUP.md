# Setup — Windows PowerShell (primary) and macOS/Linux

All commands below are run from the repository root unless stated otherwise.

## 1. Prerequisites (Windows)

Open **PowerShell** and install the tools with `winget`:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id UB-Mannheim.TesseractOCR -e
```

Close and reopen PowerShell so the new `PATH` entries load, then check:

```powershell
uv --version
node --version
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
```

`uv` installs Python 3.12 for the project automatically (`uv python install 3.12` if you want it explicitly).

**Tesseract language data.** The installer ships English (`eng`), which is what this release supports.
For other scripts (for example Hindi), tick the extra language in the UB-Mannheim installer or copy the
`.traineddata` file into `C:\Program Files\Tesseract-OCR\tessdata`, then set `TESSERACT_LANG=eng+hin`.
Additional languages are not tested by this project.

**PostgreSQL.** Use Neon (recommended, step 3). For a local server instead:
`winget install --id PostgreSQL.PostgreSQL.16 -e` or Docker Desktop (`docker compose up db`).

## 2. Install dependencies (locked)

```powershell
cd backend
uv sync                      # installs from uv.lock, including the pinned spaCy model en_core_web_sm
cd ..\frontend
npm ci                       # installs from package-lock.json
cd ..
```

No NLP model is downloaded at runtime; `en_core_web_sm` is pinned in `backend/pyproject.toml`.

## 3. Configure

```powershell
Copy-Item backend\.env.example backend\.env
cd backend
uv run python -m app.cli gen-secrets     # paste the three lines into backend\.env
```

Edit `backend\.env`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon **pooled** URL, e.g. `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/nbkyc?sslmode=require` |
| `MIGRATION_DATABASE_URL` | Neon **direct** (non-pooled) URL — used only by Alembic |
| `SESSION_SECRET`, `ENCRYPTION_KEY`, `IDENTITY_HMAC_SECRET` | from `gen-secrets`. Keep them out of the database and source control; losing `ENCRYPTION_KEY` makes stored files and extracted values unreadable |
| `GEMINI_API_KEY` | your key from Google AI Studio (backend only) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| `ALLOWED_ORIGINS`, `PUBLIC_BASE_URL` | `http://localhost:5173` for local development |

TLS (`sslmode=require`) is enforced automatically for `*.neon.tech` hosts. There is no fallback to SQLite: if
Neon is unreachable the app reports the error. To use SQLite for local development you must opt in explicitly:

```powershell
# in backend\.env
DATABASE_URL=sqlite:///./var/dev.db
ALLOW_SQLITE=true
```

With SQLite run exactly **one** worker.

## 4. Preflight, migrate, create the first administrator

```powershell
cd backend
uv run python -m app.cli preflight       # checks Python, secrets, Tesseract, spaCy, Gemini key, DB, migrations
uv run python -m app.cli migrate         # alembic upgrade head (creates schema + seeds "Northbridge Finance")
uv run python -m app.cli bootstrap-admin --email you@yourbank.example --name "Your Name"
```

`bootstrap-admin` prompts for a password (hidden) and enforces the password policy. No default passwords exist.
Further staff are invited from **Staff portal → Staff**; they set their own password through an emailed link.

Preflight distinguishes a missing Gemini key (`Gemini API key ... LLM_NOT_CONFIGURED`) from missing OCR
(`Tesseract OCR ... not found`). Neither silently switches the app into a demo mode.

## 5. Run (three terminals)

```powershell
# Terminal 1 — API
cd backend
uv run uvicorn app.main:app --port 8000 --reload
```

```powershell
# Terminal 2 — durable worker (Windows-compatible; Ctrl+C stops it cleanly)
cd backend
uv run python -m app.cli worker
```

```powershell
# Terminal 3 — frontend (proxies /v1 to the API so cookies stay same-origin)
cd frontend
npm run dev
```

Open <http://localhost:5173>. Staff sign in at <http://localhost:5173/staff/login>.

Without SMTP settings, verification and password-reset emails appear in the clearly labelled
**development mailbox** at <http://localhost:5173/dev/mailbox> (disabled in production).

## 6. Tests

```powershell
cd backend
uv run pytest                                          # unit + integration (SQLite); Gemini is mocked
$env:TEST_POSTGRES_URL = "postgresql://user:pass@localhost:5432/nbkyc_test"
uv run pytest -m postgres                              # PostgreSQL concurrency / migrations / constraints
$env:GEMINI_API_KEY_LIVE = "<your key>"
uv run pytest -m live_gemini                           # opt-in live call with masked SYNTHETIC text
```

`TEST_POSTGRES_URL` must point at a database you can create schemas in; each test uses a throwaway schema.

```powershell
cd frontend
npm test                                               # Vitest + React Testing Library
```

End-to-end (Playwright) runs against a stack in **test mode** with the scripted extraction test double:

Set `$env:E2E_DATABASE_URL` to a **dedicated test database** in each terminal first. Never point the E2E
environment at real data.

```powershell
# Terminal A — API in test mode
. .\scripts\e2e-env.ps1
cd backend
uv run python -m app.cli migrate
uv run uvicorn app.main:app --port 8000
```

```powershell
# Terminal B — worker in test mode
. .\scripts\e2e-env.ps1
cd backend
uv run python -m app.cli worker
```

```powershell
# Terminal C — frontend, then the tests
cd frontend
npm run dev
```

```powershell
# Terminal D
. .\scripts\e2e-env.ps1
cd frontend
npx playwright install chromium            # once; or: $env:PW_CHANNEL = "chrome" to use installed Chrome
npx playwright test
```

## 7. Evaluation on synthetic documents

```powershell
cd backend
uv run python ..\scripts\generate_samples.py           # 24 synthetic documents + text-layer copies
uv run python ..\scripts\evaluate.py --set text_pdf    # scripted test double, no network
uv run python ..\scripts\evaluate.py --set mixed       # needs Tesseract for PNG/JPEG/scanned PDF
```

## 8. Docker

```powershell
Copy-Item backend\.env.example backend\.env   # then fill in secrets as in step 3
docker compose up --build
```

The stack serves the app at <http://localhost:8080>. The backend image installs Tesseract (`eng`). For Neon, set
`DATABASE_URL` in `backend\.env` and remove the `db` service from `docker-compose.yml`.

---

## macOS / Linux (secondary)

```bash
brew install uv node tesseract        # macOS; on Debian/Ubuntu: apt install tesseract-ocr tesseract-ocr-eng
cd backend && uv sync && cp .env.example .env && uv run python -m app.cli gen-secrets
uv run python -m app.cli preflight && uv run python -m app.cli migrate
uv run python -m app.cli bootstrap-admin --email you@yourbank.example --name "Your Name"
uv run uvicorn app.main:app --port 8000 --reload      # terminal 1
uv run python -m app.cli worker                       # terminal 2
cd ../frontend && npm ci && npm run dev               # terminal 3
```

E2E on macOS/Linux: `source scripts/e2e-env.sh`, then start the processes as above, or use
`scripts/run-e2e-api.sh` and `scripts/run-e2e-worker.sh`.

Note: Homebrew no longer ships bottles for Intel Macs, so `brew install tesseract` can fail there; use the
Docker image or an Apple-silicon/Linux/Windows machine for OCR.
