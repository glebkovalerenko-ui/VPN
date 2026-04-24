"""Candidate selection queries for Stage 5 prober."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(slots=True, frozen=True)
class ProbeCandidate:
    """DB row subset required by prober checker backend."""

    id: str
    raw_config: str
    protocol: str
    host: str | None
    port: int | None
    source_country_tag: str | None
    last_seen_at: datetime
    last_checked_at: datetime | None


def select_candidates_for_probe(session: Session, *, batch_size: int) -> list[ProbeCandidate]:
    """Select deterministic probe batch preferring never-checked or stale entries."""
    rows = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT
                    pc.candidate_id,
                    MAX(pc.checked_at) AS last_checked_at
                FROM proxy_checks AS pc
                GROUP BY pc.candidate_id
            )
            SELECT
                c.id,
                c.raw_config,
                c.protocol,
                c.host,
                c.port,
                c.source_country_tag,
                c.last_seen_at,
                lc.last_checked_at
            FROM proxy_candidates AS c
            LEFT JOIN latest_checks AS lc
                ON lc.candidate_id = c.id
            WHERE c.is_enabled = TRUE
            ORDER BY
                (lc.last_checked_at IS NULL) DESC,
                lc.last_checked_at ASC NULLS FIRST,
                c.last_seen_at DESC,
                c.id ASC
            LIMIT :batch_size
            """
        ),
        {"batch_size": batch_size},
    ).mappings().all()

    return [
        ProbeCandidate(
            id=str(row["id"]),
            raw_config=str(row["raw_config"]),
            protocol=str(row["protocol"]).lower(),
            host=row["host"],
            port=row["port"],
            source_country_tag=row["source_country_tag"],
            last_seen_at=row["last_seen_at"],
            last_checked_at=row["last_checked_at"],
        )
        for row in rows
    ]
