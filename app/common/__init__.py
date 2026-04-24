"""Public exports for the shared foundation layer."""

from .db import check_db_connection, create_db_engine, get_engine, get_session_factory, session_scope
from .enums import ProxyStatus, SourceFamily
from .logging import configure_logging, get_logger
from .models import ProxyCandidate, ProxyCheck, ProxyState, Source, SourceSnapshot
from .settings import Settings, get_settings, settings

__all__ = [
    "Settings",
    "settings",
    "get_settings",
    "create_db_engine",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "check_db_connection",
    "SourceFamily",
    "ProxyStatus",
    "Source",
    "SourceSnapshot",
    "ProxyCandidate",
    "ProxyCheck",
    "ProxyState",
    "configure_logging",
    "get_logger",
]

