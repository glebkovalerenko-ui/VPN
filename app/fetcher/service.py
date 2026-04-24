"""Fetcher service for downloading and snapshotting upstream TXT sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

import requests
from requests import Session as HttpSession
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import session_scope
from app.common.logging import get_logger
from app.common.settings import Settings, get_settings

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ActiveSource:
    """Minimal source record required by fetcher."""

    id: str
    name: str
    url: str
    last_checksum: str | None


@dataclass(slots=True, frozen=True)
class FetchCycleStats:
    """Execution stats for a single fetch cycle."""

    total_active: int
    processed: int
    changed: int
    unchanged: int
    failed: int

    def to_log_extra(self) -> dict[str, int]:
        return {
            "total_active": self.total_active,
            "processed": self.processed,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
        }


def compute_checksum(raw_text: str) -> str:
    """Compute SHA-256 checksum for raw source text."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def fetch_active_sources(session: Session) -> list[ActiveSource]:
    """Load active upstream sources from DB."""
    query = text(
        """
        SELECT id, name, url, last_checksum
        FROM sources
        WHERE is_active = TRUE
        ORDER BY name
        """
    )
    rows = session.execute(query).mappings().all()
    return [
        ActiveSource(
            id=str(row["id"]),
            name=str(row["name"]),
            url=str(row["url"]),
            last_checksum=row["last_checksum"],
        )
        for row in rows
    ]


def fetch_source_snapshot(
    source: ActiveSource,
    timeout_seconds: int,
    http_session: HttpSession,
) -> tuple[str, int]:
    """Download source payload and return UTF-8 text + HTTP status."""
    response = http_session.get(source.url, timeout=timeout_seconds)
    response.raise_for_status()
    raw_text = response.content.decode("utf-8", errors="replace")
    return raw_text, response.status_code


def persist_source_fetch_result(
    session: Session,
    source: ActiveSource,
    *,
    fetched_at: datetime,
    checksum: str,
    raw_text: str,
) -> bool:
    """Persist source metadata and optional snapshot. Returns changed flag."""
    changed = checksum != source.last_checksum

    if changed:
        session.execute(
            text(
                """
                INSERT INTO source_snapshots (source_id, fetched_at, checksum, raw_content)
                VALUES (:source_id, :fetched_at, :checksum, :raw_content)
                """
            ),
            {
                "source_id": source.id,
                "fetched_at": fetched_at,
                "checksum": checksum,
                "raw_content": raw_text,
            },
        )

        session.execute(
            text(
                """
                UPDATE sources
                SET last_fetched_at = :fetched_at,
                    last_checksum = :checksum,
                    updated_at = :updated_at
                WHERE id = :source_id
                """
            ),
            {
                "source_id": source.id,
                "fetched_at": fetched_at,
                "checksum": checksum,
                "updated_at": fetched_at,
            },
        )
    else:
        session.execute(
            text(
                """
                UPDATE sources
                SET last_fetched_at = :fetched_at,
                    updated_at = :updated_at
                WHERE id = :source_id
                """
            ),
            {
                "source_id": source.id,
                "fetched_at": fetched_at,
                "updated_at": fetched_at,
            },
        )

    return changed


def run_fetch_cycle(app_settings: Settings | None = None) -> FetchCycleStats:
    """Run single fetch cycle over all active sources."""
    settings = app_settings or get_settings()
    timeout_seconds = settings.DOWNLOAD_TIMEOUT_SECONDS

    with session_scope(settings) as session:
        active_sources = fetch_active_sources(session)

    logger.info(
        "Fetcher cycle started",
        extra={"active_sources": len(active_sources), "timeout_seconds": timeout_seconds},
    )

    processed = 0
    changed_count = 0
    unchanged_count = 0
    failed = 0

    with requests.Session() as http_session:
        for source in active_sources:
            logger.info(
                "Processing source",
                extra={"source_name": source.name, "source_url": source.url},
            )
            try:
                raw_text, status_code = fetch_source_snapshot(
                    source,
                    timeout_seconds=timeout_seconds,
                    http_session=http_session,
                )
                checksum = compute_checksum(raw_text)
                fetched_at = datetime.now(timezone.utc)

                logger.info(
                    "Source fetched successfully",
                    extra={
                        "source_name": source.name,
                        "http_status": status_code,
                        "text_length": len(raw_text),
                        "checksum": checksum,
                    },
                )

                if raw_text == "":
                    logger.warning(
                        "Fetched source is empty",
                        extra={"source_name": source.name, "source_url": source.url},
                    )

                with session_scope(settings) as source_session:
                    changed = persist_source_fetch_result(
                        source_session,
                        source,
                        fetched_at=fetched_at,
                        checksum=checksum,
                        raw_text=raw_text,
                    )

                processed += 1
                if changed:
                    changed_count += 1
                    logger.info(
                        "Source content changed, snapshot created",
                        extra={"source_name": source.name, "checksum": checksum},
                    )
                else:
                    unchanged_count += 1
                    logger.info(
                        "Source content unchanged, snapshot skipped",
                        extra={"source_name": source.name, "checksum": checksum},
                    )
            except requests.RequestException:
                failed += 1
                logger.exception(
                    "Source fetch failed",
                    extra={"source_name": source.name, "source_url": source.url},
                )
            except Exception:
                failed += 1
                logger.exception(
                    "Unexpected source processing error",
                    extra={"source_name": source.name, "source_url": source.url},
                )

    stats = FetchCycleStats(
        total_active=len(active_sources),
        processed=processed,
        changed=changed_count,
        unchanged=unchanged_count,
        failed=failed,
    )
    logger.info("Fetcher cycle finished", extra=stats.to_log_extra())
    return stats
