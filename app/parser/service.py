"""Parser service for Stage 4 snapshot-to-candidate normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import session_scope
from app.common.logging import get_logger
from app.common.settings import Settings, get_settings

from .fingerprint import build_fingerprint
from .parsers import ParsedProxyLine, parse_proxy_line
from .utils import looks_like_header_line, normalize_input_line

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class SourceLatestSnapshot:
    """Source row with latest snapshot payload."""

    source_id: str
    source_name: str
    family: str
    snapshot_id: str | None
    fetched_at: datetime | None
    raw_content: str | None


@dataclass(slots=True)
class ParserCycleStats:
    """Execution metrics for one parser run."""

    sources_seen: int = 0
    snapshots_seen: int = 0
    lines_total: int = 0
    lines_skipped: int = 0
    candidates_inserted: int = 0
    candidates_updated: int = 0
    parse_errors: int = 0

    def to_log_extra(self) -> dict[str, int]:
        return {
            "sources_seen": self.sources_seen,
            "snapshots_seen": self.snapshots_seen,
            "lines_total": self.lines_total,
            "lines_skipped": self.lines_skipped,
            "candidates_inserted": self.candidates_inserted,
            "candidates_updated": self.candidates_updated,
            "parse_errors": self.parse_errors,
        }


def fetch_active_sources_latest_snapshots(session: Session) -> list[SourceLatestSnapshot]:
    """Load all active sources with latest available snapshot (if any)."""
    rows = session.execute(
        text(
            """
            SELECT
                s.id AS source_id,
                s.name AS source_name,
                s.family AS family,
                latest.id AS snapshot_id,
                latest.fetched_at AS fetched_at,
                latest.raw_content AS raw_content
            FROM sources AS s
            LEFT JOIN LATERAL (
                SELECT ss.id, ss.fetched_at, ss.raw_content
                FROM source_snapshots AS ss
                WHERE ss.source_id = s.id
                ORDER BY ss.fetched_at DESC
                LIMIT 1
            ) AS latest ON TRUE
            WHERE s.is_active = TRUE
            ORDER BY s.name
            """
        )
    ).mappings().all()

    return [
        SourceLatestSnapshot(
            source_id=str(row["source_id"]),
            source_name=str(row["source_name"]),
            family=str(row["family"]),
            snapshot_id=str(row["snapshot_id"]) if row["snapshot_id"] else None,
            fetched_at=row["fetched_at"],
            raw_content=str(row["raw_content"]) if row["raw_content"] is not None else None,
        )
        for row in rows
    ]


def run_parse_cycle(app_settings: Settings | None = None) -> ParserCycleStats:
    """Run one parser cycle over latest source snapshots."""
    settings = app_settings or get_settings()
    stats = ParserCycleStats()
    observed_at = datetime.now(timezone.utc)

    with session_scope(settings) as session:
        source_rows = fetch_active_sources_latest_snapshots(session)
        stats.sources_seen = len(source_rows)

        logger.info("Parser cycle started", extra={"sources_seen": stats.sources_seen})

        for source in source_rows:
            if source.snapshot_id is None or source.raw_content is None:
                logger.info(
                    "Skipping source without snapshots",
                    extra={"source_name": source.source_name, "source_id": source.source_id},
                )
                continue

            stats.snapshots_seen += 1
            _process_snapshot(
                session=session,
                source=source,
                observed_at=observed_at,
                stats=stats,
            )

    logger.info("Parser cycle finished", extra=stats.to_log_extra())
    return stats


def _process_snapshot(
    *,
    session: Session,
    source: SourceLatestSnapshot,
    observed_at: datetime,
    stats: ParserCycleStats,
) -> None:
    logger.info(
        "Processing snapshot",
        extra={
            "source_name": source.source_name,
            "source_id": source.source_id,
            "snapshot_id": source.snapshot_id,
            "snapshot_fetched_at": source.fetched_at,
        },
    )

    for line_number, raw_line in enumerate(source.raw_content.splitlines(), start=1):
        stats.lines_total += 1
        line = normalize_input_line(raw_line)

        if not line or looks_like_header_line(line):
            stats.lines_skipped += 1
            continue

        try:
            parsed = parse_proxy_line(line)
        except Exception:
            stats.parse_errors += 1
            logger.exception(
                "Failed to parse line",
                extra={
                    "source_name": source.source_name,
                    "line_number": line_number,
                    "line_preview": line[:160],
                },
            )
            continue

        if parsed is None:
            stats.lines_skipped += 1
            logger.debug(
                "Skipping unsupported line",
                extra={
                    "source_name": source.source_name,
                    "line_number": line_number,
                    "line_preview": line[:160],
                },
            )
            continue

        if parsed.partially_parsed:
            logger.warning(
                "Config parsed partially",
                extra={
                    "source_name": source.source_name,
                    "line_number": line_number,
                    "protocol": parsed.protocol,
                    "host": parsed.host,
                    "port": parsed.port,
                },
            )

        inserted = upsert_proxy_candidate(
            session=session,
            source=source,
            candidate=parsed,
            observed_at=observed_at,
        )
        if inserted:
            stats.candidates_inserted += 1
        else:
            stats.candidates_updated += 1


def upsert_proxy_candidate(
    *,
    session: Session,
    source: SourceLatestSnapshot,
    candidate: ParsedProxyLine,
    observed_at: datetime,
) -> bool:
    """Insert new candidate or update last_seen_at for existing fingerprint."""
    fingerprint = build_fingerprint(candidate)
    inserted = session.execute(
        text(
            """
            INSERT INTO proxy_candidates (
                fingerprint,
                raw_config,
                protocol,
                host,
                port,
                sni,
                family,
                source_country_tag,
                source_id,
                first_seen_at,
                last_seen_at
            )
            VALUES (
                :fingerprint,
                :raw_config,
                :protocol,
                :host,
                :port,
                :sni,
                :family,
                :source_country_tag,
                :source_id,
                :first_seen_at,
                :last_seen_at
            )
            ON CONFLICT (fingerprint)
            DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                source_id = EXCLUDED.source_id,
                family = EXCLUDED.family,
                source_country_tag = COALESCE(EXCLUDED.source_country_tag, proxy_candidates.source_country_tag)
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {
            "fingerprint": fingerprint,
            "raw_config": candidate.raw_config,
            "protocol": candidate.protocol,
            "host": candidate.host,
            "port": candidate.port,
            "sni": candidate.sni,
            "family": source.family,
            "source_country_tag": candidate.source_country_tag,
            "source_id": source.source_id,
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
        },
    ).scalar_one()
    return bool(inserted)
