"""Dependency helpers for Stage 10 HTTP API."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends
from sqlalchemy.orm import Session

from app.common.db import get_session_factory
from app.common.settings import PROJECT_ROOT, Settings, get_settings

OUTPUT_DIR_NAME = "output"
MANIFEST_FILE_NAME = "export_manifest.json"


def get_app_settings() -> Settings:
    """Return shared application settings."""
    return get_settings()


def get_db_session(settings: Settings = Depends(get_app_settings)) -> Iterator[Session]:
    """Yield a read-only session for API handlers."""
    session = get_session_factory(settings)()
    try:
        yield session
    finally:
        session.close()


def get_output_dir() -> Path:
    """Return absolute path to exports output directory."""
    return (PROJECT_ROOT / OUTPUT_DIR_NAME).resolve()


def get_manifest_path(output_dir: Path = Depends(get_output_dir)) -> Path:
    """Return absolute path to export manifest JSON file."""
    return (output_dir / MANIFEST_FILE_NAME).resolve()

