"""Database engine and session management.

One database per environment: the URL in DATABASE_URL is used as-is. There is no
fallback to SQLite when PostgreSQL is unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def normalize_url(url: str) -> str:
    """Use the psycopg (v3) driver for PostgreSQL URLs and enforce TLS for Neon hosts."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    u = make_url(url)
    if u.drivername.startswith("postgresql") and u.host and u.host.endswith(".neon.tech"):
        q = dict(u.query)
        if q.get("sslmode") not in ("require", "verify-ca", "verify-full"):
            q["sslmode"] = "require"
        # psycopg does not understand Neon's channel_binding hint in every version; keep it if given.
        u = u.set(query=q)
    return u.render_as_string(hide_password=False)


def build_engine(url: str, *, pool_size: int = 5) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine
    return create_engine(url, pool_size=pool_size, max_overflow=5, pool_pre_ping=True, pool_recycle=300)


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    s.validate_database_url()
    return build_engine(s.database_url, pool_size=s.db_pool_size)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"
