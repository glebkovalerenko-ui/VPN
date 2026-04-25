"""Operational latest-check speed diagnostics."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.common.db import session_scope
from app.common.settings import get_settings


def main() -> int:
    """Print latest-check speed diagnostic counters as JSON."""
    settings = get_settings()
    with session_scope(settings) as session:
        payload = {
            "latest_speed_summary": _fetch_latest_speed_summary(session),
            "speed_semantics_breakdown": _fetch_speed_semantics_breakdown(session),
            "speed_error_code_breakdown": _fetch_speed_error_code_breakdown(session),
        }

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _fetch_latest_speed_summary(session: Any) -> dict[str, int]:
    row = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.connect_ok,
                    pc.download_mbps,
                    pc.speed_attempts,
                    pc.speed_successes,
                    pc.speed_error_code,
                    pc.speed_failure_reason
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                COUNT(*)::int AS latest_checks,
                COUNT(*) FILTER (WHERE connect_ok = TRUE)::int AS connect_ok,
                COUNT(*) FILTER (WHERE connect_ok = FALSE)::int AS connect_failed,
                COUNT(*) FILTER (
                    WHERE connect_ok = TRUE
                      AND download_mbps IS NULL
                      AND COALESCE(speed_attempts, 0) = 0
                      AND speed_error_code IS NULL
                      AND speed_failure_reason IS NULL
                )::int AS empty_speed_null_without_reason,
                COUNT(*) FILTER (
                    WHERE connect_ok = TRUE
                      AND (
                        download_mbps IS NOT NULL
                        OR COALESCE(speed_attempts, 0) > 0
                        OR COALESCE(speed_successes, 0) > 0
                        OR speed_error_code IS NOT NULL
                        OR speed_failure_reason IS NOT NULL
                      )
                )::int AS new_speed_semantics,
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
    return {key: int(row[key] or 0) for key in row.keys()}


def _fetch_speed_semantics_breakdown(session: Any) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.connect_ok,
                    pc.download_mbps,
                    pc.speed_attempts,
                    pc.speed_successes,
                    pc.speed_error_code,
                    pc.speed_failure_reason
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                CASE
                    WHEN connect_ok = FALSE THEN 'connect_failed'
                    WHEN connect_ok = TRUE AND download_mbps IS NOT NULL THEN 'measured'
                    WHEN connect_ok = TRUE
                      AND download_mbps IS NULL
                      AND COALESCE(speed_attempts, 0) = 0
                      AND COALESCE(speed_successes, 0) = 0
                      AND speed_error_code IS NULL
                      AND speed_failure_reason IS NULL
                    THEN 'legacy_no_speed_diagnostics'
                    WHEN connect_ok = TRUE AND download_mbps IS NULL THEN 'diagnosed_unavailable'
                    ELSE 'unknown'
                END AS speed_semantics,
                COUNT(*)::int AS cnt
            FROM latest_checks
            GROUP BY 1
            ORDER BY cnt DESC, speed_semantics
            """
        )
    ).mappings().all()
    return {str(row["speed_semantics"]): int(row["cnt"]) for row in rows}


def _fetch_speed_error_code_breakdown(session: Any) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            WITH latest_checks AS (
                SELECT DISTINCT ON (pc.candidate_id)
                    pc.candidate_id,
                    pc.speed_error_code
                FROM proxy_checks AS pc
                ORDER BY pc.candidate_id, pc.checked_at DESC, pc.id DESC
            )
            SELECT
                COALESCE(speed_error_code, 'speed_error_code_null') AS speed_error_code,
                COUNT(*)::int AS cnt
            FROM latest_checks
            GROUP BY 1
            ORDER BY cnt DESC, speed_error_code
            """
        )
    ).mappings().all()
    return {str(row["speed_error_code"]): int(row["cnt"]) for row in rows}


if __name__ == "__main__":
    raise SystemExit(main())
