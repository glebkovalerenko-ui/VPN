"""Aggregation layer for Stage 8 scorer."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import CandidateAggregation, CandidateRecord, CheckRecord

_RATIO_SCALE = Decimal("0.0001")
_SPEED_SCALE = Decimal("0.001")


def fetch_candidates(session: Session) -> list[CandidateRecord]:
    """Return all proxy candidates participating in state computation."""
    rows = session.execute(
        text(
            """
            SELECT id, family
            FROM proxy_candidates
            WHERE is_enabled = TRUE
            ORDER BY id ASC
            """
        )
    ).mappings().all()

    return [
        CandidateRecord(
            id=str(row["id"]),
            family=str(row["family"]),
        )
        for row in rows
    ]


def fetch_recent_checks_by_candidate(
    session: Session,
    *,
    recent_limit: int,
) -> dict[str, list[CheckRecord]]:
    """Load recent probe history per candidate using deterministic windowing."""
    rows = session.execute(
        text(
            """
            WITH ranked_checks AS (
                SELECT
                    pc.id,
                    pc.candidate_id,
                    pc.checked_at,
                    pc.connect_ok,
                    pc.connect_ms,
                    pc.download_mbps,
                    pc.exit_country,
                    pc.geo_match,
                    ROW_NUMBER() OVER (
                        PARTITION BY pc.candidate_id
                        ORDER BY pc.checked_at DESC, pc.id DESC
                    ) AS rn
                FROM proxy_checks AS pc
            )
            SELECT
                id,
                candidate_id,
                checked_at,
                connect_ok,
                connect_ms,
                download_mbps,
                exit_country,
                geo_match
            FROM ranked_checks
            WHERE rn <= :recent_limit
            ORDER BY candidate_id ASC, checked_at DESC, id DESC
            """
        ),
        {"recent_limit": recent_limit},
    ).mappings().all()

    checks_by_candidate: defaultdict[str, list[CheckRecord]] = defaultdict(list)
    for row in rows:
        checks_by_candidate[str(row["candidate_id"])].append(
            CheckRecord(
                id=str(row["id"]),
                candidate_id=str(row["candidate_id"]),
                checked_at=row["checked_at"],
                connect_ok=bool(row["connect_ok"]),
                connect_ms=row["connect_ms"],
                download_mbps=row["download_mbps"],
                exit_country=row["exit_country"],
                geo_match=row["geo_match"],
            )
        )

    return dict(checks_by_candidate)


def aggregate_candidate(
    candidate: CandidateRecord,
    checks: list[CheckRecord],
) -> CandidateAggregation:
    """Build aggregated metrics for one candidate from recent checks."""
    if not checks:
        return CandidateAggregation(
            candidate_id=candidate.id,
            family=candidate.family,
            checks_total=0,
            checks_successful=0,
            last_check_at=None,
            last_success_at=None,
            current_country=None,
            latency_ms=None,
            download_mbps=None,
            stability_ratio=None,
            geo_confidence=None,
        )

    ordered_checks = sorted(
        checks,
        key=lambda item: (item.checked_at, item.id),
        reverse=True,
    )
    successful_checks = [check for check in ordered_checks if check.connect_ok]

    latencies = [check.connect_ms for check in successful_checks if check.connect_ms is not None]
    speeds = [check.download_mbps for check in successful_checks if check.download_mbps is not None]
    geo_samples = [1 if check.geo_match else 0 for check in successful_checks if check.geo_match is not None]

    last_success = successful_checks[0] if successful_checks else None
    checks_total = len(ordered_checks)
    checks_successful = len(successful_checks)

    return CandidateAggregation(
        candidate_id=candidate.id,
        family=candidate.family,
        checks_total=checks_total,
        checks_successful=checks_successful,
        last_check_at=ordered_checks[0].checked_at,
        last_success_at=last_success.checked_at if last_success else None,
        current_country=last_success.exit_country if last_success else None,
        latency_ms=_median_int(latencies),
        download_mbps=_median_decimal(speeds),
        stability_ratio=_ratio(checks_successful, checks_total),
        geo_confidence=_ratio(sum(geo_samples), len(geo_samples)) if geo_samples else None,
    )


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None

    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]

    average = (Decimal(ordered[mid - 1]) + Decimal(ordered[mid])) / Decimal("2")
    return int(average.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None

    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid].quantize(_SPEED_SCALE, rounding=ROUND_HALF_UP)

    average = (ordered[mid - 1] + ordered[mid]) / Decimal("2")
    return average.quantize(_SPEED_SCALE, rounding=ROUND_HALF_UP)


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(_RATIO_SCALE, rounding=ROUND_HALF_UP)
