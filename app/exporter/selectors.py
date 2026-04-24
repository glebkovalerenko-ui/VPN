"""Selection queries for Stage 9 exporter."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.enums import ProxyStatus

from .models import ExportCandidate


def select_export_candidates(
    session: Session,
    *,
    status: ProxyStatus = ProxyStatus.ACTIVE,
) -> list[ExportCandidate]:
    """Load exporter input from proxy_state + proxy_candidates in deterministic order."""
    rows = session.execute(
        text(
            """
            SELECT
                ps.candidate_id,
                ps.status,
                ps.current_country,
                ps.final_score,
                ps.stability_ratio,
                ps.last_success_at,
                ps.rank_global,
                ps.rank_in_family,
                ps.rank_in_country,
                c.raw_config,
                c.family,
                c.host,
                c.fingerprint
            FROM proxy_state AS ps
            JOIN proxy_candidates AS c
                ON c.id = ps.candidate_id
            WHERE ps.status = :status
              AND ps.final_score IS NOT NULL
              AND ps.final_score > 0
              AND c.raw_config IS NOT NULL
              AND btrim(c.raw_config) <> ''
            ORDER BY
                ps.final_score DESC,
                ps.stability_ratio DESC NULLS LAST,
                ps.last_success_at DESC NULLS LAST,
                ps.candidate_id ASC
            """
        ),
        {"status": status.value},
    ).mappings().all()

    return [
        ExportCandidate(
            candidate_id=str(row["candidate_id"]),
            status=str(row["status"]),
            family=str(row["family"]),
            raw_config=str(row["raw_config"]).strip(),
            host=row["host"],
            fingerprint=row["fingerprint"],
            current_country=row["current_country"],
            final_score=row["final_score"],
            stability_ratio=row["stability_ratio"],
            last_success_at=row["last_success_at"],
            rank_global=row["rank_global"],
            rank_in_family=row["rank_in_family"],
            rank_in_country=row["rank_in_country"],
        )
        for row in rows
    ]


def fetch_proxy_state_status_counts(session: Session) -> dict[str, int]:
    """Return lightweight status breakdown for export manifest."""
    rows = session.execute(
        text(
            """
            SELECT
                status,
                COUNT(*)::int AS cnt
            FROM proxy_state
            GROUP BY status
            ORDER BY status
            """
        )
    ).mappings().all()
    return {str(row["status"]): int(row["cnt"]) for row in rows}
