"""Scoring logic for Stage 8 proxy_state computation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.common.enums import ProxyStatus
from app.common.settings import Settings

from .models import CandidateAggregation, ScoredState

_SCORE_SCALE = Decimal("0.0001")
_ONE = Decimal("1.0000")
_ZERO = Decimal("0.0000")

_WEIGHT_THROUGHPUT = Decimal("0.40")
_WEIGHT_LATENCY = Decimal("0.30")
_WEIGHT_STABILITY = Decimal("0.20")
_WEIGHT_GEO = Decimal("0.10")

_STATUS_PENALTIES: dict[ProxyStatus, Decimal] = {
    ProxyStatus.DEGRADED: Decimal("0.1500"),
    ProxyStatus.DEAD: Decimal("1.1000"),
    ProxyStatus.UNKNOWN: Decimal("1.2500"),
}


def validate_scorer_settings(settings: Settings) -> None:
    """Validate scorer settings relationships that cannot be expressed by Field bounds."""
    if settings.SCORER_LATENCY_GOOD_MS >= settings.SCORER_LATENCY_BAD_MS:
        raise ValueError("SCORER_LATENCY_GOOD_MS must be less than SCORER_LATENCY_BAD_MS")
    if settings.SCORER_SPEED_GOOD_MBPS <= settings.SCORER_SPEED_BAD_MBPS:
        raise ValueError("SCORER_SPEED_GOOD_MBPS must be greater than SCORER_SPEED_BAD_MBPS")
    if settings.SCORER_MIN_DEGRADED_STABILITY > settings.SCORER_MIN_ACTIVE_STABILITY:
        raise ValueError("SCORER_MIN_DEGRADED_STABILITY must be <= SCORER_MIN_ACTIVE_STABILITY")


def score_candidate_state(
    aggregation: CandidateAggregation,
    settings: Settings,
    *,
    scored_at: datetime,
) -> ScoredState:
    """Convert aggregated candidate metrics into persisted proxy_state payload."""
    freshness_score = compute_freshness_score(
        last_success_at=aggregation.last_success_at,
        last_check_at=aggregation.last_check_at,
        scored_at=scored_at,
        check_freshness_minutes=settings.CHECK_FRESHNESS_MINUTES,
        max_selection_age_minutes=settings.MAX_SELECTION_AGE_MINUTES,
    )

    throughput_score = normalize_throughput_score(
        download_mbps=aggregation.download_mbps,
        speed_bad_mbps=settings.SCORER_SPEED_BAD_MBPS,
        speed_good_mbps=settings.SCORER_SPEED_GOOD_MBPS,
    )
    latency_score = normalize_latency_score(
        latency_ms=aggregation.latency_ms,
        latency_good_ms=settings.SCORER_LATENCY_GOOD_MS,
        latency_bad_ms=settings.SCORER_LATENCY_BAD_MS,
    )
    stability_score = _quantize_unit_score(aggregation.stability_ratio or _ZERO)
    geo_score = _quantize_unit_score(
        aggregation.geo_confidence
        if aggregation.geo_confidence is not None
        else Decimal(str(settings.SCORER_GEO_NEUTRAL_SCORE))
    )

    status = determine_status(
        aggregation=aggregation,
        freshness_score=freshness_score,
        settings=settings,
    )

    penalties = compute_penalties(
        aggregation=aggregation,
        status=status,
        freshness_score=freshness_score,
        settings=settings,
    )
    base_score = (
        (_WEIGHT_THROUGHPUT * throughput_score)
        + (_WEIGHT_LATENCY * latency_score)
        + (_WEIGHT_STABILITY * stability_score)
        + (_WEIGHT_GEO * geo_score)
    )
    final_score = _quantize_raw_score(base_score - penalties)

    return ScoredState(
        candidate_id=aggregation.candidate_id,
        family=aggregation.family,
        status=status,
        last_check_at=aggregation.last_check_at,
        last_success_at=aggregation.last_success_at,
        current_country=aggregation.current_country,
        latency_ms=aggregation.latency_ms,
        download_mbps=aggregation.download_mbps,
        stability_ratio=aggregation.stability_ratio,
        geo_confidence=aggregation.geo_confidence,
        freshness_score=freshness_score,
        final_score=final_score,
        updated_at=scored_at,
    )


def determine_status(
    *,
    aggregation: CandidateAggregation,
    freshness_score: Decimal,
    settings: Settings,
) -> ProxyStatus:
    """Map aggregated metrics to one of active/degraded/dead/unknown statuses."""
    if aggregation.checks_total == 0:
        return ProxyStatus.UNKNOWN

    if aggregation.checks_successful == 0:
        return ProxyStatus.DEAD

    stability = float(aggregation.stability_ratio or Decimal("0"))
    freshness = float(freshness_score)

    if (
        stability >= settings.SCORER_MIN_ACTIVE_STABILITY
        and freshness >= settings.SCORER_MIN_ACTIVE_FRESHNESS
    ):
        return ProxyStatus.ACTIVE

    if stability >= settings.SCORER_MIN_DEGRADED_STABILITY:
        return ProxyStatus.DEGRADED

    if freshness <= settings.SCORER_DEAD_FRESHNESS_MAX:
        return ProxyStatus.DEAD

    return ProxyStatus.DEGRADED


def compute_freshness_score(
    *,
    last_success_at: datetime | None,
    last_check_at: datetime | None,
    scored_at: datetime,
    check_freshness_minutes: int,
    max_selection_age_minutes: int,
) -> Decimal:
    """Compute 0..1 freshness score based on recency of latest success or latest check."""
    reference = last_success_at or last_check_at
    if reference is None:
        return _ZERO

    aware_scored_at = _ensure_utc(scored_at)
    aware_reference = _ensure_utc(reference)
    age_minutes = max(0.0, (aware_scored_at - aware_reference).total_seconds() / 60.0)

    if max_selection_age_minutes <= check_freshness_minutes:
        return _ONE if age_minutes <= max_selection_age_minutes else _ZERO
    if age_minutes <= check_freshness_minutes:
        return _ONE
    if age_minutes >= max_selection_age_minutes:
        return _ZERO

    fade_range = max_selection_age_minutes - check_freshness_minutes
    freshness = 1.0 - ((age_minutes - check_freshness_minutes) / fade_range)
    return _quantize_unit_score(Decimal(str(freshness)))


def normalize_throughput_score(
    *,
    download_mbps: Decimal | None,
    speed_bad_mbps: float,
    speed_good_mbps: float,
) -> Decimal:
    """Normalize speed to 0..1 with saturation."""
    if download_mbps is None:
        return _ZERO

    value = float(download_mbps)
    if value <= speed_bad_mbps:
        return _ZERO
    if value >= speed_good_mbps:
        return _ONE

    normalized = (value - speed_bad_mbps) / (speed_good_mbps - speed_bad_mbps)
    return _quantize_unit_score(Decimal(str(normalized)))


def normalize_latency_score(
    *,
    latency_ms: int | None,
    latency_good_ms: int,
    latency_bad_ms: int,
) -> Decimal:
    """Normalize latency to 0..1 where lower latency is better."""
    if latency_ms is None:
        return _ZERO

    if latency_ms <= latency_good_ms:
        return _ONE
    if latency_ms >= latency_bad_ms:
        return _ZERO

    normalized = (latency_bad_ms - latency_ms) / (latency_bad_ms - latency_good_ms)
    return _quantize_unit_score(Decimal(str(normalized)))


def compute_penalties(
    *,
    aggregation: CandidateAggregation,
    status: ProxyStatus,
    freshness_score: Decimal,
    settings: Settings,
) -> Decimal:
    """Compute additive penalties for stale, incomplete, and unhealthy states."""
    freshness_penalty = (_ONE - freshness_score) * Decimal(str(settings.SCORER_FRESHNESS_PENALTY_WEIGHT))
    missing_speed_penalty = (
        Decimal(str(settings.SCORER_MISSING_SPEED_PENALTY))
        if aggregation.download_mbps is None
        else _ZERO
    )
    status_penalty = _STATUS_PENALTIES.get(status, _ZERO)
    return _quantize_raw_score(freshness_penalty + missing_speed_penalty + status_penalty)


def _quantize_unit_score(value: Decimal) -> Decimal:
    clamped = value
    if clamped < _ZERO:
        clamped = _ZERO
    if clamped > _ONE:
        clamped = _ONE
    return clamped.quantize(_SCORE_SCALE, rounding=ROUND_HALF_UP)


def _quantize_raw_score(value: Decimal) -> Decimal:
    return value.quantize(_SCORE_SCALE, rounding=ROUND_HALF_UP)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
