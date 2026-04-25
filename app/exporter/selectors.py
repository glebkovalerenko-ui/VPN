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
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.speed_error_code,
                    pc.speed_failure_reason,
                    pc.speed_error_text,
                    pc.speed_endpoint_url,
                    pc.speed_attempts,
                    pc.speed_successes
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                ps.candidate_id,
                ps.status,
                ps.current_country,
                ps.final_score,
                ps.stability_ratio,
                ps.latency_ms,
                ps.download_mbps,
                ps.geo_confidence,
                ps.freshness_score,
                ps.last_success_at,
                ps.rank_global,
                ps.rank_in_family,
                ps.rank_in_country,
                c.raw_config,
                c.family,
                c.host,
                c.fingerprint,
                lc.speed_error_code,
                lc.speed_failure_reason,
                lc.speed_error_text,
                lc.speed_endpoint_url,
                lc.speed_attempts,
                lc.speed_successes
            FROM proxy_state AS ps
            JOIN proxy_candidates AS c
                ON c.id = ps.candidate_id
            LEFT JOIN latest_checks AS lc
                ON lc.candidate_id = ps.candidate_id
            WHERE ps.status = :status
              AND ps.final_score IS NOT NULL
              AND ps.final_score > 0
              AND c.is_enabled = TRUE
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
            latency_ms=row["latency_ms"],
            download_mbps=row["download_mbps"],
            speed_error_code=row["speed_error_code"],
            speed_failure_reason=row["speed_failure_reason"],
            speed_error_text=row["speed_error_text"],
            speed_endpoint_url=row["speed_endpoint_url"],
            speed_attempts=int(row["speed_attempts"] or 0),
            speed_successes=int(row["speed_successes"] or 0),
            geo_confidence=row["geo_confidence"],
            freshness_score=row["freshness_score"],
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


def fetch_speed_quality_summary(session: Session) -> dict[str, object]:
    """Return latest-check speed quality counters for debug/export visibility."""
    totals = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.connect_ok,
                    pc.download_mbps
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                COUNT(*)::int AS latest_checks,
                COUNT(*) FILTER (WHERE connect_ok = TRUE)::int AS connect_ok,
                COUNT(*) FILTER (WHERE connect_ok = FALSE)::int AS connect_failed,
                COUNT(*) FILTER (
                    WHERE connect_ok = TRUE
                      AND download_mbps IS NOT NULL
                )::int AS speed_measured,
                COUNT(*) FILTER (
                    WHERE connect_ok = TRUE
                      AND download_mbps IS NULL
                )::int AS speed_unavailable
            FROM latest_checks
            """
        )
    ).mappings().one()

    reason_rows = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.connect_ok,
                    pc.download_mbps,
                    pc.speed_error_code,
                    pc.speed_failure_reason
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                COALESCE(speed_failure_reason, speed_error_code, 'speed_not_available') AS reason,
                COUNT(*)::int AS cnt
            FROM latest_checks
            WHERE connect_ok = TRUE
              AND download_mbps IS NULL
            GROUP BY reason
            ORDER BY reason
            """
        )
    ).mappings().all()

    return {
        "latest_checks": int(totals["latest_checks"] or 0),
        "connect_ok": int(totals["connect_ok"] or 0),
        "connect_failed": int(totals["connect_failed"] or 0),
        "speed_measured": int(totals["speed_measured"] or 0),
        "speed_unavailable": int(totals["speed_unavailable"] or 0),
        "speed_unavailable_by_reason": {
            str(row["reason"]): int(row["cnt"])
            for row in reason_rows
        },
    }
