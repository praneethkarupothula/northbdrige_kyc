"""Command-line tools. Run ``python -m app.cli --help`` (``uv run python -m app.cli ...``).

  preflight        check Python, Tesseract, spaCy model, database, migrations, secrets, Gemini config
  gen-secrets      print fresh values for SESSION_SECRET / ENCRYPTION_KEY / IDENTITY_HMAC_SECRET
  bootstrap-admin  create the first administrator (password prompted; never a default)
  worker           run the durable background worker
  migrate          alembic upgrade head
  openapi          write the OpenAPI schema to docs/openapi.json
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _p(ok: bool | None, label: str, detail: str = "") -> bool:
    mark = {True: "[ OK ]", False: "[FAIL]", None: "[WARN]"}[ok]
    print(f"{mark} {label}" + (f" - {detail}" if detail else ""), flush=True)
    return ok is not False


def migration_status() -> tuple[str | None, str | None]:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.core.db import get_engine

    cfg = Config(str(BACKEND / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with get_engine().connect() as conn:
        cur = MigrationContext.configure(conn).get_current_revision()
    return cur, head


def preflight(_args) -> int:
    ok = True
    print("Northbridge KYC preflight", flush=True)
    ok &= _p(sys.version_info[:2] == (3, 12), "Python 3.12", sys.version.split()[0])
    try:
        from app.core.config import get_settings

        s = get_settings()
        _p(True, "Configuration loaded", f"APP_ENV={s.app_env}")
    except Exception as exc:  # noqa: BLE001
        _p(False, "Configuration", str(exc).splitlines()[0])
        return 1
    # secrets
    try:
        s.encryption_key_bytes()
        _p(True, "ENCRYPTION_KEY")
    except Exception as exc:  # noqa: BLE001
        ok &= _p(False, "ENCRYPTION_KEY", str(exc))
    try:
        s.hmac_key_bytes()
        _p(True, "IDENTITY_HMAC_SECRET")
    except Exception as exc:  # noqa: BLE001
        ok &= _p(False, "IDENTITY_HMAC_SECRET", str(exc))
    ok &= _p(len(s.session_secret.get_secret_value()) >= 32, "SESSION_SECRET", "at least 32 characters")
    # OCR (independent of Gemini)
    from app.pipeline.ocr import tesseract_available

    t_ok, t_msg = tesseract_available()
    ok &= _p(t_ok, "Tesseract OCR", t_msg)
    if t_ok:
        import pytesseract

        langs = pytesseract.get_languages(config="")
        missing = [lang for lang in s.tesseract_lang.split("+") if lang not in langs]
        ok &= _p(not missing, f"Tesseract language data ({s.tesseract_lang})",
                 "installed" if not missing else f"missing: {missing}")
    # spaCy model (never downloaded at runtime)
    try:
        import spacy

        spacy.load("en_core_web_sm")
        _p(True, "spaCy model en_core_web_sm")
    except Exception:  # noqa: BLE001
        ok &= _p(False, "spaCy model en_core_web_sm", "not installed - run: uv sync (model is pinned in pyproject)")
    # Gemini (distinct from OCR problems)
    if s.llm_provider == "scripted":
        _p(None, "LLM provider", "scripted TEST DOUBLE (APP_ENV=test only) - no Gemini calls")
    elif s.gemini_api_key.get_secret_value():
        _p(True, "Gemini API key present", f"model={s.gemini_model} (key validity is checked on first call)")
    else:
        ok &= _p(False, "Gemini API key", "GEMINI_API_KEY not set - processing will fail with LLM_NOT_CONFIGURED "
                 "(uploads, OCR and review still work). No demo fallback is used.")
    # database
    try:
        s.validate_database_url()
        from sqlalchemy import text

        from app.core.db import get_engine

        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
            dialect = c.dialect.name
        _p(True, "Database reachable", dialect)
        cur, head = migration_status()
        ok &= _p(cur == head, "Migrations", f"db={cur} head={head}" + ("" if cur == head else " - run: python -m app.cli migrate"))
        if dialect == "sqlite":
            _p(None, "SQLite", "development only; run exactly ONE worker")
    except Exception as exc:  # noqa: BLE001
        ok &= _p(False, "Database", f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
    # storage
    try:
        s.storage_dir.mkdir(parents=True, exist_ok=True)
        probe = s.storage_dir / ".write-test"
        probe.write_text("x")
        probe.unlink()
        _p(True, "Storage directory writable", "private, outside any web root")
    except Exception as exc:  # noqa: BLE001
        ok &= _p(False, "Storage directory", type(exc).__name__)
    _p(None if s.dev_email_capture else True, "Email",
       "development capture (no SMTP configured)" if s.dev_email_capture else f"SMTP {s.smtp_host}")
    print("Preflight " + ("passed" if ok else "FAILED"), flush=True)
    return 0 if ok else 1


def gen_secrets(_args) -> int:
    print("SESSION_SECRET=" + secrets.token_urlsafe(48))
    print("ENCRYPTION_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="))
    print("IDENTITY_HMAC_SECRET=" + secrets.token_urlsafe(48))
    return 0


def bootstrap_admin(args) -> int:
    from sqlalchemy import select

    from app.auth.passwords import hash_password, password_problems
    from app.core.db import session_scope
    from app.models import Role, StaffMembership, User
    from app.services.audit import audit
    from app.services.policy import default_org

    email = (args.email or input("Admin email: ")).strip().lower()
    name = (args.name or input("Full name: ")).strip()
    pw = os.environ.get("NB_BOOTSTRAP_PASSWORD") or getpass.getpass("Password (hidden): ")
    if not os.environ.get("NB_BOOTSTRAP_PASSWORD") and getpass.getpass("Confirm password: ") != pw:
        print("Passwords do not match.")
        return 1
    problems = password_problems(pw, email=email, full_name=name)
    if problems:
        print("Weak password: " + " ".join(problems))
        return 1
    from datetime import UTC, datetime

    with session_scope() as db:
        org = default_org(db)
        if db.scalar(select(User).where(User.email == email)):
            print("A user with this email already exists.")
            return 1
        u = User(email=email, full_name=name, role=Role.ADMIN, password_hash=hash_password(pw),
                 email_verified_at=datetime.now(UTC))
        db.add(u)
        db.flush()
        db.add(StaffMembership(user_id=u.id, organization_id=org.id, role=Role.ADMIN))
        audit(db, action="staff.bootstrap_admin", target_type="user", target_id=u.id, organization_id=org.id)
    print(f"Administrator created for {org.name}. Sign in at /staff/login.")
    return 0


def worker(args) -> int:
    from app.core.config import get_settings
    from app.observability.telemetry import configure_logging, configure_tracing

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    configure_tracing(s.otel_service_name + "-worker", s.otel_exporter_otlp_endpoint)
    print("[worker] loading configuration", flush=True)
    s.validate_database_url()
    from app.pipeline.ocr import tesseract_available

    ok, msg = tesseract_available()
    print(f"[worker] tesseract: {msg}", flush=True)
    if not ok:
        if s.app_env == "production":
            print("[worker] ERROR: Tesseract is required for OCR. Install it or set TESSERACT_CMD.", flush=True)
            return 2
        print("[worker] WARNING: Tesseract not found. Text-layer PDFs still work; images and scanned PDFs will "
              "fail with OCR_UNAVAILABLE (a system error, never a mismatch). Install Tesseract or set TESSERACT_CMD.",
              flush=True)
    print("[worker] loading Presidio + spaCy model (local, no download)...", flush=True)
    from app.pipeline.pii import get_analyzer

    get_analyzer()
    if s.llm_provider == "gemini" and not s.gemini_api_key.get_secret_value():
        print("[worker] WARNING: GEMINI_API_KEY is not set; jobs will fail with LLM_NOT_CONFIGURED "
              "(never a fabricated result).", flush=True)
    from app.workers.runner import Worker

    w = Worker()
    w.install_signal_handlers()
    print(f"[worker] ready as {w.worker_id}; polling for jobs (Ctrl+C to stop)", flush=True)
    w.run_forever()
    return 0


def migrate(_args) -> int:
    from alembic import command
    from alembic.config import Config

    print("[migrate] alembic upgrade head", flush=True)
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    print("[migrate] done", flush=True)
    return 0


def openapi(args) -> int:
    import json

    from app.main import create_app

    out = Path(args.out)
    out.write_text(json.dumps(create_app().openapi(), indent=2))
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight").set_defaults(fn=preflight)
    sub.add_parser("gen-secrets").set_defaults(fn=gen_secrets)
    b = sub.add_parser("bootstrap-admin")
    b.add_argument("--email")
    b.add_argument("--name")
    b.set_defaults(fn=bootstrap_admin)
    sub.add_parser("worker").set_defaults(fn=worker)
    sub.add_parser("migrate").set_defaults(fn=migrate)
    o = sub.add_parser("openapi")
    o.add_argument("--out", default=str(BACKEND.parent / "docs" / "openapi.json"))
    o.set_defaults(fn=openapi)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
