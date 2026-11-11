from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.db import normalize_url
from app.models import Base

target_metadata = Base.metadata


def _url() -> str:
    override = context.config.attributes.get("database_url")
    if override:
        return override
    s = get_settings()
    s.validate_database_url()
    return s.migration_database_url or s.database_url


def run_migrations_offline() -> None:
    context.configure(url=normalize_url(_url()), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = normalize_url(_url())
    engine = create_engine(url, poolclass=pool.NullPool)  # no pooling for migrations
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
