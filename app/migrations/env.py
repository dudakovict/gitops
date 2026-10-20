"""Alembic environment. Uses a synchronous psycopg connection; the URL comes from DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from docqa.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Serialize concurrent migrators. The lock is session-level, so it survives the commit;
        # the commit matters because otherwise this statement auto-begins a transaction that
        # Alembic would run inside of and never commit.
        connection.exec_driver_sql("SELECT pg_advisory_lock(727001)")
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.exec_driver_sql("SELECT pg_advisory_unlock(727001)")
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
