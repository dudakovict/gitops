"""Async engine/session factory for the services, plus the migration entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine, _sessions
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url.get_secret_value(), pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    engine()
    assert _sessions is not None
    return _sessions


@asynccontextmanager
async def session() -> AsyncGenerator[AsyncSession]:
    async with session_factory()() as s:
        yield s


async def dispose() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessions = None


def migrate() -> None:
    """`docqa-migrate`: apply Alembic migrations. Run as a Job before rolling out a new version."""
    import logging
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from .logging import configure_logging

    configure_logging()
    logging.getLogger("alembic").setLevel(logging.INFO)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    command.upgrade(cfg, "head")
