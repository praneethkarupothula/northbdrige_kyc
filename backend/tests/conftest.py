"""Test fixtures.

* Default DB: a fresh SQLite file per test, copied from a template migrated with Alembic.
* PostgreSQL: tests marked ``postgres`` use TEST_POSTGRES_URL (skipped if unset) and run the
  real Alembic migrations against a fresh schema.
* The LLM is the scripted test double (APP_ENV=test); Gemini is mocked in dedicated tests.
"""

from __future__ import annotations

import base64
import os
import shutil
import uuid
from pathlib import Path

import pytest

os.environ.update({
    "APP_ENV": "test",
    "LLM_PROVIDER": "scripted",
    "ALLOW_SQLITE": "true",
    "SESSION_SECRET": "test-session-secret-" + "x" * 32,
    "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
    "IDENTITY_HMAC_SECRET": "test-hmac-secret-" + "y" * 32,
    "LOG_JSON": "true",
    "GEMINI_MAX_RETRIES": "1",
    "WORKER_HEARTBEAT_SECONDS": "1",
    "SMTP_HOST": "",
})

BACKEND = Path(__file__).resolve().parents[1]
_TEMPLATE: Path | None = None


def _reset_caches():
    from app.core import config, db
    from app.storage import blob

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    blob.get_store.cache_clear()


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def sqlite_template(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("tpl") / "template.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    _reset_caches()
    _migrate(os.environ["DATABASE_URL"])
    return path


@pytest.fixture
def env(request, tmp_path, sqlite_template, monkeypatch):
    """Fresh database + storage per test. Use ``@pytest.mark.postgres`` to run on PostgreSQL."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    pg_engine = None
    if request.node.get_closest_marker("postgres"):
        base = os.environ.get("TEST_POSTGRES_URL")
        if not base:
            pytest.skip("TEST_POSTGRES_URL not set")
        from sqlalchemy import create_engine, text

        from app.core.db import normalize_url

        schema = "t_" + uuid.uuid4().hex[:10]
        pg_engine = create_engine(normalize_url(base), isolation_level="AUTOCOMMIT")
        with pg_engine.connect() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        sep = "&" if "?" in base else "?"
        url = f"{base}{sep}options=-csearch_path%3D{schema}"
        monkeypatch.setenv("DATABASE_URL", url)
        _reset_caches()
        _migrate(url)
    else:
        db_path = tmp_path / "test.db"
        shutil.copy(sqlite_template, db_path)
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        _reset_caches()
    yield
    from app.core.db import get_engine

    get_engine().dispose()
    if pg_engine is not None:
        with pg_engine.connect() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        pg_engine.dispose()
    _reset_caches()


class Api:
    """TestClient wrapper that sends the double-submit CSRF header automatically."""

    def __init__(self, client):
        self.c = client
        self.c.get("/v1/auth/csrf")

    def _h(self, headers=None):
        h = dict(headers or {})
        tok = self.c.cookies.get("nb_csrf")
        if tok:
            h["X-CSRF-Token"] = tok
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)


@pytest.fixture
def app(env):
    from app.main import create_app

    return create_app()


@pytest.fixture
def make_client(app):
    from fastapi.testclient import TestClient

    clients = []

    def _make():
        c = TestClient(app)
        clients.append(c)
        return Api(c)

    yield _make
    for c in clients:
        c.close()


PASSWORD = "Correct-Horse-42!"


@pytest.fixture
def applicant(make_client):
    """Factory: registers an applicant and returns a signed-in client."""

    def _applicant(name="Asha Verma", email=None):
        api = make_client()
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = api.post("/v1/auth/register", json={"full_name": name, "email": email, "password": PASSWORD,
                                                "password_confirm": PASSWORD})
        assert r.status_code == 201, r.text
        api.email = email
        return api

    return _applicant


def create_staff(role="reviewer", email=None, org_slug="northbridge"):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.auth.passwords import hash_password
    from app.core.db import session_scope
    from app.models import Organization, StaffMembership, User

    email = email or f"{role}-{uuid.uuid4().hex[:6]}@northbridge.example.com"
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == org_slug))
        if org is None:
            org = Organization(slug=org_slug, name=org_slug.title())
            db.add(org)
            db.flush()
        u = User(email=email, full_name=f"Test {role.title()}", role=role, password_hash=hash_password(PASSWORD),
                 email_verified_at=datetime.now(UTC))
        db.add(u)
        db.flush()
        db.add(StaffMembership(user_id=u.id, organization_id=org.id, role=role))
    return email


@pytest.fixture
def staff(make_client):
    def _staff(role="reviewer", org_slug="northbridge"):
        email = create_staff(role, org_slug=org_slug)
        api = make_client()
        r = api.post("/v1/auth/staff/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        api.email = email
        return api

    return _staff


@pytest.fixture
def worker(env):
    from app.workers.runner import Worker

    return Worker(worker_id="test-worker")


requires_ocr = pytest.mark.skipif(not (os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")),
                                  reason="Tesseract binary not installed")
