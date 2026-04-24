"""Shared SQLAlchemy engine/session utilities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import Settings, get_settings

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def create_db_engine(database_url: str, connect_timeout: int | None = None) -> Engine:
    """Create SQLAlchemy engine suitable for services and CLI tools."""
    connect_args: dict[str, int] = {}
    if connect_timeout is not None:
        connect_args["connect_timeout"] = connect_timeout

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def get_engine(app_settings: Settings | None = None) -> Engine:
    """Return shared engine instance."""
    global _ENGINE
    if _ENGINE is None:
        settings = app_settings or get_settings()
        _ENGINE = create_db_engine(
            settings.database_url,
            connect_timeout=settings.CONNECT_TIMEOUT_SECONDS,
        )
    return _ENGINE


def get_session_factory(app_settings: Settings | None = None) -> sessionmaker[Session]:
    """Return shared session factory."""
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(
            bind=get_engine(app_settings),
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SESSION_FACTORY


@contextmanager
def session_scope(app_settings: Settings | None = None) -> Iterator[Session]:
    """Provide transactional session scope."""
    session = get_session_factory(app_settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db_connection(engine: Engine | None = None) -> tuple[bool, str]:
    """Run lightweight DB connectivity check."""
    active_engine = engine or get_engine()
    try:
        with active_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)
