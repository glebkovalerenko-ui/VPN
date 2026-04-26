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
    raw_config: str | None
    host: str | None
    fingerprint: str | None
    source_country_tag: str | None
    is_enabled: bool
    current_country: str | None
    final_score: Decimal | None
    stability_ratio: Decimal | None
    latency_ms: int | None
    download_mbps: Decimal | None
    latest_check_checked_at: datetime | None
    latest_check_connect_ok: bool | None
    latest_check_first_byte_ms: int | None
    latest_check_download_mbps: Decimal | None
    latest_check_exit_country: str | None
    latest_check_geo_match: bool | None
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


@dataclass(slots=True, frozen=True)
class RejectedExportItem:
    """Rejected candidate enriched with the exact selection decision."""

    rejection_stage: str
    primary_rejection_reason: str
    rejection_reasons: tuple[str, ...]
    selection_country_group: str | None
    selection_host_group: str | None
    candidate: ExportCandidate


@dataclass(slots=True)
class ExportSelectionSummary:
    """Explain counters collected while applying diversity limits."""

    considered: int
    selected: int
    limit: int
    max_per_country: int
    max_per_host: int
    max_latency_ms: int
    min_download_mbps: Decimal
    require_speed_measurement: bool
    min_freshness_score: Decimal
    min_final_score_exclusive: Decimal
    rejected_before_diversity: int
    disabled_candidate_skipped: int
    low_final_score_skipped: int
    latency_threshold_skipped: int
    missing_speed_skipped: int
    low_speed_skipped: int
    freshness_threshold_skipped: int
    legacy_no_speed_semantics_skipped: int
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
    rejected_items: list[RejectedExportItem]
    summary: ExportSelectionSummary
