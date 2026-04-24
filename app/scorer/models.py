"""Domain models used by Stage 8 scorer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.common.enums import ProxyStatus


@dataclass(slots=True, frozen=True)
class CandidateRecord:
    """Minimal candidate metadata required by scorer."""

    id: str
    family: str


@dataclass(slots=True, frozen=True)
class CheckRecord:
    """Recent probe check row used for aggregation."""

    id: str
    candidate_id: str
    checked_at: datetime
    connect_ok: bool
    connect_ms: int | None
    download_mbps: Decimal | None
    exit_country: str | None
    geo_match: bool | None


@dataclass(slots=True, frozen=True)
class CandidateAggregation:
    """Aggregated current state before scoring."""

    candidate_id: str
    family: str
    checks_total: int
    checks_successful: int
    last_check_at: datetime | None
    last_success_at: datetime | None
    current_country: str | None
    latency_ms: int | None
    download_mbps: Decimal | None
    stability_ratio: Decimal | None
    geo_confidence: Decimal | None


@dataclass(slots=True, frozen=True)
class ScoredState:
    """Scored proxy_state payload ready to persist."""

    candidate_id: str
    family: str
    status: ProxyStatus
    last_check_at: datetime | None
    last_success_at: datetime | None
    current_country: str | None
    latency_ms: int | None
    download_mbps: Decimal | None
    stability_ratio: Decimal | None
    geo_confidence: Decimal | None
    freshness_score: Decimal
    final_score: Decimal
    updated_at: datetime

