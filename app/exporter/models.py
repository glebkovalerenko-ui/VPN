"""Domain models used by Stage 9 exporter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class ExportCandidate:
    """Joined candidate/state row required for export selection."""

    candidate_id: str
    status: str
    family: str
    raw_config: str
    host: str | None
    fingerprint: str | None
    current_country: str | None
    final_score: Decimal
    stability_ratio: Decimal | None
    latency_ms: int | None
    download_mbps: Decimal | None
    speed_error_code: str | None
    speed_failure_reason: str | None
    speed_error_text: str | None
    speed_endpoint_url: str | None
    speed_attempts: int
    speed_successes: int
    geo_confidence: Decimal | None
    freshness_score: Decimal | None
    last_success_at: datetime | None
    rank_global: int | None
    rank_in_family: int | None
    rank_in_country: int | None


@dataclass(slots=True, frozen=True)
class SelectedExportItem:
    """Selected candidate enriched with grouping metadata used during selection."""

    selection_position: int
    selection_country_group: str
    selection_host_group: str
    candidate: ExportCandidate


@dataclass(slots=True)
class ExportSelectionSummary:
    """Explain counters collected while applying diversity limits."""

    considered: int
    selected: int
    limit: int
    max_per_country: int
    max_per_host: int
    dedup_raw_config_skipped: int
    country_limit_skipped: int
    host_limit_skipped: int
    empty_or_invalid_skipped: int
    eligible_before_diversity: int
    selected_after_diversity: int


@dataclass(slots=True, frozen=True)
class ExportSelectionResult:
    """Final ordered selection plus explainability metadata."""

    selected_candidates: list[ExportCandidate]
    selected_items: list[SelectedExportItem]
    summary: ExportSelectionSummary
