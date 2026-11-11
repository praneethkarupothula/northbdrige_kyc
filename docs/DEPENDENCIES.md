# Dependency inventory

Generated from `backend/pyproject.toml` and `frontend/package.json`.
Exact resolved and transitive versions live in `backend/uv.lock` and `frontend/package-lock.json`.

## System prerequisites

- Python >=3.12,<3.13; uv; Git.
- Node 22 or 24 with npm.
- Tesseract OCR executable and English language data.
- SQLite for local development, or PostgreSQL for server/multi-worker use.
- Gemini API access for normal automated extraction.
- Optional: Docker/Compose; Playwright Chromium; boto3 plus an S3-compatible bucket for S3 storage.

## Backend runtime

- `fastapi>=0.115`
- `uvicorn[standard]>=0.30`
- `pydantic>=2.8`
- `pydantic-settings>=2.4`
- `email-validator>=2.2`
- `sqlalchemy>=2.0.32`
- `alembic>=1.13`
- `psycopg[binary]>=3.2`
- `argon2-cffi>=23.1`
- `cryptography>=43`
- `python-multipart>=0.0.9`
- `pymupdf>=1.24.10,<2`
- `pytesseract>=0.3.13`
- `pillow>=10.4`
- `opencv-python-headless>=4.10`
- `numpy>=1.26`
- `presidio-analyzer>=2.2.355`
- `presidio-anonymizer>=2.2.355`
- `spacy>=3.7`
- `google-genai>=1.0`
- `structlog>=24.4`
- `opentelemetry-api>=1.27`
- `opentelemetry-sdk>=1.27`
- `opentelemetry-exporter-otlp-proto-http>=1.27`
- `rapidfuzz>=3.9`
- `https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`

## Backend development

- `pytest>=8.3`
- `pytest-timeout>=2.3`
- `httpx>=0.27`
- `reportlab>=4.2`
- `ruff>=0.6`

## Frontend runtime

- `@fontsource/ibm-plex-sans`: `^5.3.0`
- `@fontsource/ibm-plex-serif`: `^5.3.0`
- `@hookform/resolvers`: `^3.10.0`
- `@radix-ui/react-checkbox`: `^1.4.1`
- `@radix-ui/react-dialog`: `^1.3.1`
- `@radix-ui/react-dropdown-menu`: `^2.2.1`
- `@radix-ui/react-label`: `^2.1.17`
- `@radix-ui/react-progress`: `^1.2.1`
- `@radix-ui/react-select`: `^2.4.1`
- `@radix-ui/react-separator`: `^1.2.0`
- `@radix-ui/react-slot`: `^1.5.0`
- `@radix-ui/react-tabs`: `^1.2.1`
- `@radix-ui/react-tooltip`: `^1.4.1`
- `@tanstack/react-query`: `^5.107.1`
- `class-variance-authority`: `^0.7.1`
- `clsx`: `^2.1.1`
- `lucide-react`: `^1.70.0`
- `react`: `^19.1.0`
- `react-dom`: `^19.1.0`
- `react-hook-form`: `^7.91.0`
- `react-router-dom`: `^7.18.5`
- `sonner`: `^2.0.8`
- `tailwind-merge`: `^3.7.0`
- `zod`: `^3.25.76`

## Frontend development

- `@eslint/js`: `^9.25.0`
- `@playwright/test`: `^1.65.0`
- `@tailwindcss/vite`: `^4.3.5`
- `@testing-library/jest-dom`: `^7.0.1`
- `@testing-library/react`: `^16.3.3`
- `@testing-library/user-event`: `^14.6.8`
- `@types/node`: `^26.6.6`
- `@types/react`: `^19.1.2`
- `@types/react-dom`: `^19.1.2`
- `@vitejs/plugin-react`: `^4.4.1`
- `eslint`: `^9.25.0`
- `eslint-plugin-react-hooks`: `^5.2.0`
- `eslint-plugin-react-refresh`: `^0.4.19`
- `globals`: `^16.0.0`
- `jsdom`: `^31.0.0`
- `tailwindcss`: `^4.3.5`
- `tw-animate-css`: `^1.4.0`
- `typescript`: `~5.8.3`
- `typescript-eslint`: `^8.30.1`
- `vite`: `^6.3.5`
- `vitest`: `^3.2.7`

## Installation

```bash
cd backend
uv sync --locked
cd ../frontend
npm ci
```

The pinned spaCy model is downloaded from the URL declared in pyproject.toml during setup.
Tesseract must be installed separately at the OS level. boto3 is optional and is not included in the default lockfile.

Regenerate this inventory and the project report with `uv run python ../scripts/build_project_report.py` from `backend`.
