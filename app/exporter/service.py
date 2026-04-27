"""Stage 9 exporter orchestration service."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.common.db import session_scope
from app.common.enums import ProxyStatus, SourceFamily
from app.common.logging import get_logger
from app.common.settings import PROJECT_ROOT, Settings, get_settings

from .models import (
    ExportCandidate,
    ExportSelectionResult,
    ExportSelectionSummary,
    RejectedExportItem,
    SelectedExportItem,
)
from .relabel import RelabeledRawLink, build_relabeled_raw_link
from .selectors import (
    fetch_multihost_quality_summary,
    fetch_proxy_state_status_counts,
    fetch_speed_quality_summary,
    select_export_candidates,
)
from .writer import write_json_atomic, write_txt_atomic

logger = get_logger(__name__)

BLACK_FILE = "BLACK-ETALON.txt"
WHITE_CIDR_FILE = "WHITE-CIDR-ETALON.txt"
WHITE_SNI_FILE = "WHITE-SNI-ETALON.txt"
ALL_FILE = "ALL-ETALON.txt"
MANIFEST_FILE = "export_manifest.json"
BLACK_DEBUG_FILE = "BLACK-ETALON-debug.json"
WHITE_CIDR_DEBUG_FILE = "WHITE-CIDR-ETALON-debug.json"
WHITE_SNI_DEBUG_FILE = "WHITE-SNI-ETALON-debug.json"
ALL_DEBUG_FILE = "ALL-ETALON-debug.json"
_OUTPUT_FILES: tuple[str, ...] = (
    BLACK_FILE,
    WHITE_CIDR_FILE,
    WHITE_SNI_FILE,
    ALL_FILE,
)
_DEBUG_FILE_BY_EXPORT: dict[str, str] = {
    BLACK_FILE: BLACK_DEBUG_FILE,
    WHITE_CIDR_FILE: WHITE_CIDR_DEBUG_FILE,
    WHITE_SNI_FILE: WHITE_SNI_DEBUG_FILE,
    ALL_FILE: ALL_DEBUG_FILE,
}

_UNKNOWN_COUNTRY_GROUP = "__unknown_country__"


@dataclass(slots=True)
class ExportCycleStats:
    """Execution metrics for one exporter run."""

    considered_candidates: int = 0
    selected_total_across_files: int = 0
    selected_unique_candidates: int = 0
    black_selected: int = 0
    white_cidr_selected: int = 0
    white_sni_selected: int = 0
    all_selected: int = 0
    output_dir: str = ""
    manifest_path: str = ""

    def to_log_extra(self) -> dict[str, int | str]:
        return {
            "considered_candidates": self.considered_candidates,
            "selected_total_across_files": self.selected_total_across_files,
            "selected_unique_candidates": self.selected_unique_candidates,
            "black_selected": self.black_selected,
            "white_cidr_selected": self.white_cidr_selected,
            "white_sni_selected": self.white_sni_selected,
            "all_selected": self.all_selected,
            "output_dir": self.output_dir,
            "manifest_path": self.manifest_path,
        }


@dataclass(slots=True, frozen=True)
class LastGoodFallback:
    """Resolution outcome for last-good export fallback policy."""

    use_fallback: bool
    reason: str | None
    lines_by_file: dict[str, list[str]]


@dataclass(slots=True, frozen=True)
class ExportPolicy:
    """Hardening thresholds applied on top of proxy_state score ranking."""

    max_per_country: int
    max_per_host: int
    max_latency_ms: int
    max_first_byte_ms: int
    min_download_mbps: Decimal
    require_speed_measurement: bool
    require_latest_check_success: bool
    max_latest_check_age_minutes: int
    require_last_two_successes: bool
    recent_checks_window: int
    min_recent_success_ratio: Decimal
    min_user_target_success_ratio: Decimal
    require_critical_targets_all_success: bool
    min_critical_target_success_ratio: Decimal
    min_freshness_score: Decimal
    min_final_score_exclusive: Decimal = Decimal("0.0000")


def run_export_cycle(app_settings: Settings | None = None) -> ExportCycleStats:
    """Run one Stage 9 export cycle using proxy_state as ranking source of truth."""
    settings = app_settings or get_settings()
    generated_at = datetime.now(timezone.utc)
    stats = ExportCycleStats()
    export_policy = _build_export_policy(settings)

    with session_scope(settings) as session:
        eligible_candidates = select_export_candidates(
            session,
            status=ProxyStatus.ACTIVE,
            recent_checks_window=export_policy.recent_checks_window,
        )
        status_counts = fetch_proxy_state_status_counts(session)
        speed_quality_summary = fetch_speed_quality_summary(session)
        multihost_quality_summary = fetch_multihost_quality_summary(
            session,
            min_user_target_success_ratio=float(export_policy.min_user_target_success_ratio),
            require_critical_targets_all_success=export_policy.require_critical_targets_all_success,
            min_critical_target_success_ratio=float(export_policy.min_critical_target_success_ratio),
        )

    by_family = {
        SourceFamily.BLACK.value: [
            candidate for candidate in eligible_candidates if candidate.family == SourceFamily.BLACK.value
        ],
        SourceFamily.WHITE_CIDR.value: [
            candidate for candidate in eligible_candidates if candidate.family == SourceFamily.WHITE_CIDR.value
        ],
        SourceFamily.WHITE_SNI.value: [
            candidate for candidate in eligible_candidates if candidate.family == SourceFamily.WHITE_SNI.value
        ],
    }

    selection_results: dict[str, ExportSelectionResult] = {
        BLACK_FILE: _apply_diversity_limits(
            by_family[SourceFamily.BLACK.value],
            limit=settings.EXPORT_BLACK_LIMIT,
            policy=export_policy,
            evaluated_at=generated_at,
        ),
        WHITE_CIDR_FILE: _apply_diversity_limits(
            by_family[SourceFamily.WHITE_CIDR.value],
            limit=settings.EXPORT_WHITE_CIDR_LIMIT,
            policy=export_policy,
            evaluated_at=generated_at,
        ),
        WHITE_SNI_FILE: _apply_diversity_limits(
            by_family[SourceFamily.WHITE_SNI.value],
            limit=settings.EXPORT_WHITE_SNI_LIMIT,
            policy=export_policy,
            evaluated_at=generated_at,
        ),
        ALL_FILE: _apply_diversity_limits(
            eligible_candidates,
            limit=settings.EXPORT_ALL_LIMIT,
            policy=export_policy,
            evaluated_at=generated_at,
        ),
    }

    output_dir = PROJECT_ROOT / "output"
    selected_relabeled_by_file: dict[str, list[RelabeledRawLink]] = {}
    rejected_relabeled_by_file: dict[str, list[RelabeledRawLink]] = {}
    for file_name, selection_result in selection_results.items():
        selected_relabeled_by_file[file_name] = [
            build_relabeled_raw_link(item.candidate)
            for item in selection_result.selected_items
        ]
        rejected_relabeled_by_file[file_name] = [
            build_relabeled_raw_link(item.candidate)
            for item in selection_result.rejected_items
        ]

    selected_lines_by_file = {
        file_name: [
            (relabeled.export_raw_config or "").strip()
            for relabeled in selected_relabeled_by_file[file_name]
        ]
        for file_name in selection_results
    }
    fallback = _resolve_last_good_fallback(
        output_dir=output_dir,
        active_count=len(eligible_candidates),
        selected_lines_by_file=selected_lines_by_file,
    )
    if fallback.use_fallback:
        selected_lines_by_file = fallback.lines_by_file
        logger.warning(
            "Exporter used last-good fallback",
            extra={
                "fallback_reason": fallback.reason,
                "active_candidates": len(eligible_candidates),
            },
        )

    for file_name, selected_lines in selected_lines_by_file.items():
        write_txt_atomic(output_dir / file_name, selected_lines)

    for export_name, selection_result in selection_results.items():
        debug_payload = _build_debug_export_payload(
            generated_at=generated_at,
            export_name=export_name,
            export_policy=export_policy,
            selection_result=selection_result,
            selected_relabeled_links=selected_relabeled_by_file[export_name],
            rejected_relabeled_links=rejected_relabeled_by_file[export_name],
            fallback_used=fallback.use_fallback,
            fallback_reason=fallback.reason,
            exported_lines_count=len(selected_lines_by_file[export_name]),
            speed_quality_summary=speed_quality_summary,
            multihost_quality_summary=multihost_quality_summary,
        )
        write_json_atomic(output_dir / _DEBUG_FILE_BY_EXPORT[export_name], debug_payload)

    selected_unique_candidates = len(
        {
            line
            for selected_lines in selected_lines_by_file.values()
            for line in selected_lines
        }
    )

    manifest = _build_manifest(
        generated_at=generated_at,
        settings=settings,
        export_policy=export_policy,
        active_count=len(eligible_candidates),
        eligible_candidates=eligible_candidates,
        by_family=by_family,
        selected_lines_by_file=selected_lines_by_file,
        selected_unique_candidates=selected_unique_candidates,
        status_counts=status_counts,
        speed_quality_summary=speed_quality_summary,
        multihost_quality_summary=multihost_quality_summary,
        fallback_used=fallback.use_fallback,
        fallback_reason=fallback.reason,
    )
    manifest_path = output_dir / MANIFEST_FILE
    write_json_atomic(manifest_path, manifest)

    stats.considered_candidates = len(eligible_candidates)
    stats.selected_total_across_files = sum(len(items) for items in selected_lines_by_file.values())
    stats.selected_unique_candidates = selected_unique_candidates
    stats.black_selected = len(selected_lines_by_file[BLACK_FILE])
    stats.white_cidr_selected = len(selected_lines_by_file[WHITE_CIDR_FILE])
    stats.white_sni_selected = len(selected_lines_by_file[WHITE_SNI_FILE])
    stats.all_selected = len(selected_lines_by_file[ALL_FILE])
    stats.output_dir = str(output_dir)
    stats.manifest_path = str(manifest_path)

    logger.info("Exporter cycle finished", extra=stats.to_log_extra())
    return stats


def _build_export_policy(settings: Settings) -> ExportPolicy:
    return ExportPolicy(
        max_per_country=settings.EXPORT_MAX_PER_COUNTRY,
        max_per_host=settings.EXPORT_MAX_PER_HOST,
        max_latency_ms=settings.EXPORT_MAX_LATENCY_MS,
        max_first_byte_ms=settings.EXPORT_MAX_FIRST_BYTE_MS,
        min_download_mbps=Decimal(str(settings.EXPORT_MIN_DOWNLOAD_MBPS)),
        require_speed_measurement=settings.EXPORT_REQUIRE_SPEED_MEASUREMENT,
        require_latest_check_success=settings.EXPORT_REQUIRE_LATEST_CHECK_SUCCESS,
        max_latest_check_age_minutes=settings.EXPORT_MAX_LATEST_CHECK_AGE_MINUTES,
        require_last_two_successes=settings.EXPORT_REQUIRE_LAST_TWO_SUCCESSES,
        recent_checks_window=settings.EXPORT_RECENT_CHECKS_WINDOW,
        min_recent_success_ratio=Decimal(str(settings.EXPORT_MIN_RECENT_SUCCESS_RATIO)),
        min_user_target_success_ratio=Decimal(str(settings.EXPORT_MIN_USER_TARGET_SUCCESS_RATIO)),
        require_critical_targets_all_success=settings.EXPORT_REQUIRE_CRITICAL_TARGETS_ALL_SUCCESS,
        min_critical_target_success_ratio=Decimal(str(settings.EXPORT_MIN_CRITICAL_TARGET_SUCCESS_RATIO)),
        min_freshness_score=Decimal(str(settings.EXPORT_MIN_FRESHNESS_SCORE)),
    )


def _apply_diversity_limits(
    ordered_candidates: list[ExportCandidate],
    *,
    limit: int,
    policy: ExportPolicy,
    evaluated_at: datetime,
) -> ExportSelectionResult:
    selected: list[ExportCandidate] = []
    selected_items: list[SelectedExportItem] = []
    rejected_items: list[RejectedExportItem] = []
    seen_raw_configs: set[str] = set()
    country_counts: Counter[str] = Counter()
    host_counts: Counter[str] = Counter()
    summary = ExportSelectionSummary(
        considered=len(ordered_candidates),
        selected=0,
        limit=limit,
        max_per_country=policy.max_per_country,
        max_per_host=policy.max_per_host,
        max_latency_ms=policy.max_latency_ms,
        max_first_byte_ms=policy.max_first_byte_ms,
        min_download_mbps=policy.min_download_mbps,
        require_speed_measurement=policy.require_speed_measurement,
        require_latest_check_success=policy.require_latest_check_success,
        max_latest_check_age_minutes=policy.max_latest_check_age_minutes,
        require_last_two_successes=policy.require_last_two_successes,
        recent_checks_window=policy.recent_checks_window,
        min_recent_success_ratio=policy.min_recent_success_ratio,
        min_user_target_success_ratio=policy.min_user_target_success_ratio,
        require_critical_targets_all_success=policy.require_critical_targets_all_success,
        min_critical_target_success_ratio=policy.min_critical_target_success_ratio,
        min_freshness_score=policy.min_freshness_score,
        min_final_score_exclusive=policy.min_final_score_exclusive,
        rejected_before_diversity=0,
        disabled_candidate_skipped=0,
        low_final_score_skipped=0,
        latest_check_failed_skipped=0,
        stale_skipped=0,
        missing_speed_skipped=0,
        low_speed_skipped=0,
        high_latency_skipped=0,
        high_first_byte_skipped=0,
        freshness_threshold_skipped=0,
        unstable_recent_checks_skipped=0,
        low_user_target_success_ratio_skipped=0,
        critical_targets_failed_skipped=0,
        legacy_no_speed_semantics_skipped=0,
        dedup_raw_config_skipped=0,
        country_limit_skipped=0,
        host_limit_skipped=0,
        empty_or_invalid_skipped=0,
        eligible_before_diversity=0,
        selected_after_diversity=0,
    )

    for candidate in ordered_candidates:
        if len(selected) >= limit:
            break

        country_group = _country_group(candidate.current_country)
        host_group = _host_group(candidate)
        policy_rejection_reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=evaluated_at,
        )
        if policy_rejection_reasons:
            primary_reason = policy_rejection_reasons[0]
            _increment_summary_rejection_counter(summary, primary_reason, candidate)
            rejected_items.append(
                RejectedExportItem(
                    rejection_stage="hard_policy",
                    primary_rejection_reason=primary_reason,
                    rejection_reasons=tuple(policy_rejection_reasons),
                    selection_country_group=country_group,
                    selection_host_group=host_group,
                    candidate=candidate,
                )
            )
            continue

        raw_config = (candidate.raw_config or "").strip()
        if raw_config in seen_raw_configs:
            summary.dedup_raw_config_skipped += 1
            rejected_items.append(
                RejectedExportItem(
                    rejection_stage="dedup",
                    primary_rejection_reason="dedup_raw_config",
                    rejection_reasons=("dedup_raw_config",),
                    selection_country_group=country_group,
                    selection_host_group=host_group,
                    candidate=candidate,
                )
            )
            continue

        summary.eligible_before_diversity += 1
        if country_counts[country_group] >= policy.max_per_country:
            summary.country_limit_skipped += 1
            rejected_items.append(
                RejectedExportItem(
                    rejection_stage="diversity",
                    primary_rejection_reason="country_limit",
                    rejection_reasons=("country_limit",),
                    selection_country_group=country_group,
                    selection_host_group=host_group,
                    candidate=candidate,
                )
            )
            continue

        if host_counts[host_group] >= policy.max_per_host:
            summary.host_limit_skipped += 1
            rejected_items.append(
                RejectedExportItem(
                    rejection_stage="diversity",
                    primary_rejection_reason="host_limit",
                    rejection_reasons=("host_limit",),
                    selection_country_group=country_group,
                    selection_host_group=host_group,
                    candidate=candidate,
                )
            )
            continue

        selected.append(candidate)
        selected_items.append(
            SelectedExportItem(
                selection_position=len(selected),
                selection_country_group=country_group,
                selection_host_group=host_group,
                candidate=candidate,
            )
        )
        seen_raw_configs.add(raw_config)
        country_counts[country_group] += 1
        host_counts[host_group] += 1

    summary.selected = len(selected)
    summary.selected_after_diversity = len(selected)
    return ExportSelectionResult(
        selected_candidates=selected,
        selected_items=selected_items,
        rejected_items=rejected_items,
        summary=summary,
    )


def _policy_rejection_reasons(
    candidate: ExportCandidate,
    policy: ExportPolicy,
    *,
    evaluated_at: datetime,
) -> list[str]:
    reasons: list[str] = []
    raw_config = (candidate.raw_config or "").strip()

    if not candidate.is_enabled:
        reasons.append("disabled_candidate")
    if not raw_config or "\n" in raw_config or "\r" in raw_config:
        reasons.append("empty_or_invalid")
    if candidate.final_score is None or candidate.final_score <= policy.min_final_score_exclusive:
        reasons.append("low_final_score")

    latest_check_failed = (
        candidate.latest_check_checked_at is None
        or candidate.latest_check_connect_ok is not True
    )
    if policy.require_latest_check_success and latest_check_failed:
        reasons.append("latest_check_failed")

    if _is_latest_check_stale(
        latest_check_checked_at=candidate.latest_check_checked_at,
        evaluated_at=evaluated_at,
        max_latest_check_age_minutes=policy.max_latest_check_age_minutes,
    ):
        reasons.append("stale")

    if candidate.download_mbps is None or candidate.latest_check_download_mbps is None:
        if policy.require_speed_measurement:
            reasons.append("missing_speed")
            if _latest_check_speed_semantics(candidate) == "legacy_no_speed_diagnostics":
                reasons.append("legacy_no_speed_semantics")
    elif (
        candidate.download_mbps < policy.min_download_mbps
        or candidate.latest_check_download_mbps < policy.min_download_mbps
    ):
        reasons.append("low_speed")

    if (
        candidate.latency_ms is None
        or candidate.latency_ms > policy.max_latency_ms
        or candidate.latest_check_connect_ms is None
        or candidate.latest_check_connect_ms > policy.max_latency_ms
    ):
        reasons.append("high_latency")
    if (
        candidate.latest_check_first_byte_ms is None
        or candidate.latest_check_first_byte_ms > policy.max_first_byte_ms
    ):
        reasons.append("high_first_byte")
    if candidate.freshness_score is None or candidate.freshness_score < policy.min_freshness_score:
        reasons.append("freshness_threshold")

    if policy.require_last_two_successes and candidate.latest_two_checks_successful is not True:
        reasons.append("unstable_recent_checks")
    if (
        candidate.recent_checks_success_ratio is None
        or candidate.recent_checks_success_ratio < policy.min_recent_success_ratio
    ):
        reasons.append("unstable_recent_checks")

    if not _passes_multihost_user_ratio(candidate, policy):
        reasons.append("low_user_target_success_ratio")
    if not _passes_multihost_critical_policy(candidate, policy):
        reasons.append("critical_targets_failed")

    return _dedupe_reasons(reasons)


def _increment_summary_rejection_counter(
    summary: ExportSelectionSummary,
    primary_reason: str,
    candidate: ExportCandidate,
) -> None:
    summary.rejected_before_diversity += 1
    if primary_reason == "disabled_candidate":
        summary.disabled_candidate_skipped += 1
    elif primary_reason == "empty_or_invalid":
        summary.empty_or_invalid_skipped += 1
    elif primary_reason == "low_final_score":
        summary.low_final_score_skipped += 1
    elif primary_reason == "latest_check_failed":
        summary.latest_check_failed_skipped += 1
    elif primary_reason == "stale":
        summary.stale_skipped += 1
    elif primary_reason == "missing_speed":
        summary.missing_speed_skipped += 1
        if _latest_check_speed_semantics(candidate) == "legacy_no_speed_diagnostics":
            summary.legacy_no_speed_semantics_skipped += 1
    elif primary_reason == "legacy_no_speed_semantics":
        summary.legacy_no_speed_semantics_skipped += 1
    elif primary_reason == "low_speed":
        summary.low_speed_skipped += 1
    elif primary_reason == "high_latency":
        summary.high_latency_skipped += 1
    elif primary_reason == "high_first_byte":
        summary.high_first_byte_skipped += 1
    elif primary_reason == "freshness_threshold":
        summary.freshness_threshold_skipped += 1
    elif primary_reason == "unstable_recent_checks":
        summary.unstable_recent_checks_skipped += 1
    elif primary_reason == "low_user_target_success_ratio":
        summary.low_user_target_success_ratio_skipped += 1
    elif primary_reason == "critical_targets_failed":
        summary.critical_targets_failed_skipped += 1


def _is_latest_check_stale(
    *,
    latest_check_checked_at: datetime | None,
    evaluated_at: datetime,
    max_latest_check_age_minutes: int,
) -> bool:
    if latest_check_checked_at is None:
        return True
    age_seconds = (evaluated_at - latest_check_checked_at).total_seconds()
    return age_seconds > (max_latest_check_age_minutes * 60)


def _passes_multihost_user_ratio(candidate: ExportCandidate, policy: ExportPolicy) -> bool:
    if candidate.latest_check_connect_ok is not True:
        return False
    if candidate.latest_user_targets_total <= 0:
        return False
    if candidate.latest_user_targets_success_ratio is None:
        return False
    return candidate.latest_user_targets_success_ratio >= policy.min_user_target_success_ratio


def _passes_multihost_critical_policy(candidate: ExportCandidate, policy: ExportPolicy) -> bool:
    if candidate.latest_check_connect_ok is not True:
        return False
    if candidate.latest_critical_targets_total <= 0:
        return True
    if policy.require_critical_targets_all_success:
        return candidate.latest_critical_targets_all_success is True
    ratio = _critical_targets_success_ratio(candidate)
    if ratio is None:
        return False
    return ratio >= policy.min_critical_target_success_ratio


def _critical_targets_success_ratio(candidate: ExportCandidate) -> Decimal | None:
    if candidate.latest_critical_targets_total <= 0:
        return None
    return (
        Decimal(candidate.latest_critical_targets_successful)
        / Decimal(candidate.latest_critical_targets_total)
    ).quantize(Decimal("0.0001"))


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        deduped.append(reason)
        seen.add(reason)
    return deduped


def _resolve_last_good_fallback(
    *,
    output_dir: Path,
    active_count: int,
    selected_lines_by_file: dict[str, list[str]],
) -> LastGoodFallback:
    if active_count > 0:
        return LastGoodFallback(
            use_fallback=False,
            reason=None,
            lines_by_file=selected_lines_by_file,
        )

    existing_lines_by_file = {
        file_name: _read_existing_lines(output_dir / file_name)
        for file_name in _OUTPUT_FILES
    }
    existing_non_empty_total = sum(len(lines) for lines in existing_lines_by_file.values())
    if existing_non_empty_total == 0:
        return LastGoodFallback(
            use_fallback=False,
            reason="no_active_candidates_and_no_last_good_exports",
            lines_by_file=selected_lines_by_file,
        )

    return LastGoodFallback(
        use_fallback=True,
        reason="no_active_candidates_reused_last_good_exports",
        lines_by_file=existing_lines_by_file,
    )


def _read_existing_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []

    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
    return lines


def _country_group(country: str | None) -> str:
    if country is None:
        return _UNKNOWN_COUNTRY_GROUP

    normalized = country.strip().upper()
    if not normalized:
        return _UNKNOWN_COUNTRY_GROUP
    return normalized


def _host_group(candidate: ExportCandidate) -> str:
    host = (candidate.host or "").strip().lower()
    if host:
        return f"host:{host}"

    fingerprint = (candidate.fingerprint or "").strip().lower()
    if fingerprint:
        return f"fingerprint:{fingerprint}"

    return f"candidate:{candidate.candidate_id}"


def _build_debug_export_payload(
    *,
    generated_at: datetime,
    export_name: str,
    export_policy: ExportPolicy,
    selection_result: ExportSelectionResult,
    selected_relabeled_links: list[RelabeledRawLink],
    rejected_relabeled_links: list[RelabeledRawLink],
    fallback_used: bool,
    fallback_reason: str | None,
    exported_lines_count: int,
    speed_quality_summary: dict[str, object],
    multihost_quality_summary: dict[str, object],
) -> dict[str, Any]:
    summary = selection_result.summary
    summary_payload: dict[str, Any] = {
        "considered": summary.considered,
        "selected": summary.selected,
        "limit": summary.limit,
        "max_per_country": summary.max_per_country,
        "max_per_host": summary.max_per_host,
        "max_latency_ms": summary.max_latency_ms,
        "max_first_byte_ms": summary.max_first_byte_ms,
        "min_download_mbps": _decimal_to_json_number(summary.min_download_mbps),
        "require_speed_measurement": summary.require_speed_measurement,
        "require_latest_check_success": summary.require_latest_check_success,
        "max_latest_check_age_minutes": summary.max_latest_check_age_minutes,
        "require_last_two_successes": summary.require_last_two_successes,
        "recent_checks_window": summary.recent_checks_window,
        "min_recent_success_ratio": _decimal_to_json_number(summary.min_recent_success_ratio),
        "min_user_target_success_ratio": _decimal_to_json_number(summary.min_user_target_success_ratio),
        "require_critical_targets_all_success": summary.require_critical_targets_all_success,
        "min_critical_target_success_ratio": _decimal_to_json_number(summary.min_critical_target_success_ratio),
        "min_freshness_score": _decimal_to_json_number(summary.min_freshness_score),
        "min_final_score_exclusive": _decimal_to_json_number(summary.min_final_score_exclusive),
        "rejected_before_diversity": summary.rejected_before_diversity,
        "disabled_candidate_skipped": summary.disabled_candidate_skipped,
        "low_final_score_skipped": summary.low_final_score_skipped,
        "latest_check_failed_skipped": summary.latest_check_failed_skipped,
        "stale_skipped": summary.stale_skipped,
        "missing_speed_skipped": summary.missing_speed_skipped,
        "low_speed_skipped": summary.low_speed_skipped,
        "high_latency_skipped": summary.high_latency_skipped,
        "high_first_byte_skipped": summary.high_first_byte_skipped,
        "freshness_threshold_skipped": summary.freshness_threshold_skipped,
        "unstable_recent_checks_skipped": summary.unstable_recent_checks_skipped,
        "low_user_target_success_ratio_skipped": summary.low_user_target_success_ratio_skipped,
        "critical_targets_failed_skipped": summary.critical_targets_failed_skipped,
        "legacy_no_speed_semantics_skipped": summary.legacy_no_speed_semantics_skipped,
        "dedup_raw_config_skipped": summary.dedup_raw_config_skipped,
        "country_limit_skipped": summary.country_limit_skipped,
        "host_limit_skipped": summary.host_limit_skipped,
        "empty_or_invalid_skipped": summary.empty_or_invalid_skipped,
        "eligible_before_diversity": summary.eligible_before_diversity,
        "selected_after_diversity": summary.selected_after_diversity,
    }
    if fallback_used:
        summary_payload["exported_lines_count"] = exported_lines_count

    items: list[dict[str, Any]] = []
    for item, relabeled_link in zip(
        selection_result.selected_items,
        selected_relabeled_links,
        strict=True,
    ):
        payload = _candidate_debug_payload(
            item.candidate,
            relabeled_link=relabeled_link,
            selection_country_group=item.selection_country_group,
            selection_host_group=item.selection_host_group,
        )
        payload.update(
            {
                "selection_position": item.selection_position,
                "selection_decision": {
                    "decision": "selected",
                    "reason": "passed_strict_policy_and_diversity_limits",
                    "passed_policy_checks": _passed_policy_checks(
                        item.candidate,
                        export_policy,
                        evaluated_at=generated_at,
                    ),
                    "passed_multihost_policy": _passes_multihost_user_ratio(item.candidate, export_policy)
                    and _passes_multihost_critical_policy(item.candidate, export_policy),
                },
            }
        )
        items.append(payload)

    rejected_items: list[dict[str, Any]] = []
    for rejected, relabeled_link in zip(
        selection_result.rejected_items,
        rejected_relabeled_links,
        strict=True,
    ):
        payload = _candidate_debug_payload(
            rejected.candidate,
            relabeled_link=relabeled_link,
            selection_country_group=rejected.selection_country_group,
            selection_host_group=rejected.selection_host_group,
        )
        payload.update(
            {
                "selection_decision": {
                    "decision": "rejected",
                    "stage": rejected.rejection_stage,
                    "primary_reason": rejected.primary_rejection_reason,
                    "reasons": list(rejected.rejection_reasons),
                },
            }
        )
        rejected_items.append(payload)

    top_rejection_reasons = Counter(
        rejected.primary_rejection_reason
        for rejected in selection_result.rejected_items
    )
    summary_payload["top_rejection_reasons"] = [
        {"reason": reason, "count": count}
        for reason, count in top_rejection_reasons.most_common(10)
    ]

    return {
        "generated_at": generated_at.isoformat(),
        "export_name": export_name,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "policy": _export_policy_payload(export_policy),
        "summary": summary_payload,
        "speed_quality": speed_quality_summary,
        "multihost_quality": multihost_quality_summary,
        "items": items,
        "rejected_items": rejected_items,
    }


def _export_policy_payload(policy: ExportPolicy) -> dict[str, Any]:
    return {
        "status_filter": ProxyStatus.ACTIVE.value,
        "requires_enabled_candidates": True,
        "requires_non_empty_raw_config": True,
        "requires_positive_final_score": True,
        "min_final_score_exclusive": _decimal_to_json_number(policy.min_final_score_exclusive),
        "max_per_country": policy.max_per_country,
        "max_per_host": policy.max_per_host,
        "require_latest_check_success": policy.require_latest_check_success,
        "max_latest_check_age_minutes": policy.max_latest_check_age_minutes,
        "max_latency_ms": policy.max_latency_ms,
        "max_first_byte_ms": policy.max_first_byte_ms,
        "min_download_mbps": _decimal_to_json_number(policy.min_download_mbps),
        "require_speed_measurement": policy.require_speed_measurement,
        "require_last_two_successes": policy.require_last_two_successes,
        "recent_checks_window": policy.recent_checks_window,
        "min_recent_success_ratio": _decimal_to_json_number(policy.min_recent_success_ratio),
        "min_user_target_success_ratio": _decimal_to_json_number(policy.min_user_target_success_ratio),
        "require_critical_targets_all_success": policy.require_critical_targets_all_success,
        "min_critical_target_success_ratio": _decimal_to_json_number(policy.min_critical_target_success_ratio),
        "min_freshness_score": _decimal_to_json_number(policy.min_freshness_score),
        "eligibility_vs_ranking": {
            "eligibility": "hard_gates",
            "ranking": "final_score_within_eligible",
        },
        "host_group_fallback": "host, then fingerprint, then candidate_id",
        "geo_is_diagnostic_only": True,
    }


def _candidate_debug_payload(
    candidate: ExportCandidate,
    *,
    relabeled_link: RelabeledRawLink,
    selection_country_group: str | None,
    selection_host_group: str | None,
) -> dict[str, Any]:
    speed_semantics = _latest_check_speed_semantics(candidate)
    critical_ratio = _critical_targets_success_ratio(candidate)
    return {
        "candidate_id": candidate.candidate_id,
        "family": candidate.family,
        "status": candidate.status,
        "raw_config": relabeled_link.export_raw_config,
        "source_raw_config": relabeled_link.source_raw_config,
        "export_raw_config": relabeled_link.export_raw_config,
        "display_label": relabeled_link.display_label,
        "label_country": relabeled_link.label_country,
        "label_flag": relabeled_link.label_flag,
        "label_group": relabeled_link.label_group,
        "label_download_mbps": relabeled_link.label_download_mbps,
        "label_latency_ms": relabeled_link.label_latency_ms,
        "label_rank_global": relabeled_link.label_rank_global,
        "label_rank_in_family": relabeled_link.label_rank_in_family,
        "label_strategy": relabeled_link.label_strategy,
        "label_error_code": relabeled_link.label_error_code,
        "label_error_text": relabeled_link.label_error_text,
        "host": candidate.host,
        "fingerprint": candidate.fingerprint,
        "source_country_tag": candidate.source_country_tag,
        "is_enabled": candidate.is_enabled,
        "current_country": candidate.current_country,
        "exit_country": candidate.latest_check_exit_country,
        "geo_match": candidate.latest_check_geo_match,
        "selection_country_group": selection_country_group,
        "selection_host_group": selection_host_group,
        "final_score": _decimal_to_json_number(candidate.final_score),
        "stability_ratio": _decimal_to_json_number(candidate.stability_ratio),
        "geo_confidence": _decimal_to_json_number(candidate.geo_confidence),
        "freshness_score": _decimal_to_json_number(candidate.freshness_score),
        "latency_ms": candidate.latency_ms,
        "download_mbps": _decimal_to_json_number(candidate.download_mbps),
        "state_download_mbps": _decimal_to_json_number(candidate.download_mbps),
        "state_latency_ms": candidate.latency_ms,
        "state_freshness_score": _decimal_to_json_number(candidate.freshness_score),
        "state_geo_confidence": _decimal_to_json_number(candidate.geo_confidence),
        "latest_check_checked_at": _datetime_to_iso(candidate.latest_check_checked_at),
        "latest_check_connect_ok": candidate.latest_check_connect_ok,
        "latest_check_connect_ms": candidate.latest_check_connect_ms,
        "latest_check_download_mbps": _decimal_to_json_number(candidate.latest_check_download_mbps),
        "latest_check_first_byte_ms": candidate.latest_check_first_byte_ms,
        "latest_check_exit_country": candidate.latest_check_exit_country,
        "latest_check_geo_match": candidate.latest_check_geo_match,
        "latest_user_targets_total": candidate.latest_user_targets_total,
        "latest_user_targets_successful": candidate.latest_user_targets_successful,
        "latest_user_targets_success_ratio": _decimal_to_json_number(
            candidate.latest_user_targets_success_ratio
        ),
        "latest_critical_targets_total": candidate.latest_critical_targets_total,
        "latest_critical_targets_successful": candidate.latest_critical_targets_successful,
        "latest_critical_targets_success_ratio": _decimal_to_json_number(critical_ratio),
        "latest_critical_targets_all_success": candidate.latest_critical_targets_all_success,
        "latest_multihost_failure_reason": candidate.latest_multihost_failure_reason,
        "latest_multihost_summary": candidate.latest_multihost_summary,
        "passed_multihost_policy": (
            candidate.latest_user_targets_total > 0
            and candidate.latest_multihost_failure_reason is None
        ),
        "recent_checks_total": candidate.recent_checks_total,
        "recent_checks_successful": candidate.recent_checks_successful,
        "recent_checks_success_ratio": _decimal_to_json_number(candidate.recent_checks_success_ratio),
        "latest_two_checks_successful": candidate.latest_two_checks_successful,
        "latest_check_speed_attempts": candidate.speed_attempts,
        "latest_check_speed_successes": candidate.speed_successes,
        "latest_check_speed_error_code": candidate.speed_error_code,
        "latest_check_speed_failure_reason": candidate.speed_failure_reason,
        "latest_check_speed_error_text": candidate.speed_error_text,
        "latest_check_speed_endpoint_url": candidate.speed_endpoint_url,
        "latest_check_speed_semantics": speed_semantics,
        "speed_diagnostics": {
            "checked_at": _datetime_to_iso(candidate.latest_check_checked_at),
            "connect_ok": candidate.latest_check_connect_ok,
            "download_mbps": _decimal_to_json_number(candidate.latest_check_download_mbps),
            "first_byte_ms": candidate.latest_check_first_byte_ms,
            "speed_error_code": candidate.speed_error_code,
            "speed_failure_reason": candidate.speed_failure_reason,
            "speed_error_text": candidate.speed_error_text,
            "speed_endpoint_url": candidate.speed_endpoint_url,
            "speed_attempts": candidate.speed_attempts,
            "speed_successes": candidate.speed_successes,
            "speed_semantics": speed_semantics,
        },
        "last_success_at": _datetime_to_iso(candidate.last_success_at),
        "rank_global": candidate.rank_global,
        "rank_in_family": candidate.rank_in_family,
        "rank_in_country": candidate.rank_in_country,
    }


def _passed_policy_checks(
    candidate: ExportCandidate,
    policy: ExportPolicy,
    *,
    evaluated_at: datetime,
) -> dict[str, bool]:
    critical_ratio = _critical_targets_success_ratio(candidate)
    critical_policy_passed = _passes_multihost_critical_policy(candidate, policy)
    user_policy_passed = _passes_multihost_user_ratio(candidate, policy)
    return {
        "enabled_candidate": candidate.is_enabled,
        "valid_raw_config": bool((candidate.raw_config or "").strip())
        and "\n" not in (candidate.raw_config or "")
        and "\r" not in (candidate.raw_config or ""),
        "positive_final_score": candidate.final_score is not None
        and candidate.final_score > policy.min_final_score_exclusive,
        "latest_check_successful": candidate.latest_check_connect_ok is True,
        "latest_check_is_fresh": not _is_latest_check_stale(
            latest_check_checked_at=candidate.latest_check_checked_at,
            evaluated_at=evaluated_at,
            max_latest_check_age_minutes=policy.max_latest_check_age_minutes,
        ),
        "speed_measurement_available": (
            candidate.download_mbps is not None and candidate.latest_check_download_mbps is not None
            if policy.require_speed_measurement
            else True
        ),
        "meets_min_download_mbps": candidate.download_mbps is not None
        and candidate.latest_check_download_mbps is not None
        and candidate.download_mbps >= policy.min_download_mbps
        and candidate.latest_check_download_mbps >= policy.min_download_mbps,
        "meets_max_latency_ms": candidate.latency_ms is not None
        and candidate.latest_check_connect_ms is not None
        and candidate.latency_ms <= policy.max_latency_ms
        and candidate.latest_check_connect_ms <= policy.max_latency_ms,
        "meets_max_first_byte_ms": candidate.latest_check_first_byte_ms is not None
        and candidate.latest_check_first_byte_ms <= policy.max_first_byte_ms,
        "meets_min_freshness_score": candidate.freshness_score is not None
        and candidate.freshness_score >= policy.min_freshness_score,
        "latest_two_checks_successful": (
            candidate.latest_two_checks_successful is True
            if policy.require_last_two_successes
            else True
        ),
        "recent_success_ratio_ok": candidate.recent_checks_success_ratio is not None
        and candidate.recent_checks_success_ratio >= policy.min_recent_success_ratio,
        "multihost_user_ratio_ok": user_policy_passed,
        "critical_targets_policy_ok": critical_policy_passed,
        "critical_targets_success_ratio": (
            True
            if candidate.latest_critical_targets_total <= 0
            else (
                critical_ratio is not None
                and critical_ratio >= policy.min_critical_target_success_ratio
            )
        ),
        "passed_multihost_policy": user_policy_passed and critical_policy_passed,
        "geo_ignored_for_score_and_selection": True,
    }


def _latest_check_speed_semantics(candidate: ExportCandidate) -> str:
    if candidate.latest_check_checked_at is None:
        return "missing_latest_check"
    if candidate.latest_check_connect_ok is False:
        return "connect_failed"
    if candidate.latest_check_download_mbps is not None:
        return "measured"
    if (
        candidate.speed_attempts == 0
        and candidate.speed_successes == 0
        and candidate.speed_error_code is None
        and candidate.speed_failure_reason is None
    ):
        return "legacy_no_speed_diagnostics"
    return "diagnosed_unavailable"


def _decimal_to_json_number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _build_manifest(
    *,
    generated_at: datetime,
    settings: Settings,
    export_policy: ExportPolicy,
    active_count: int,
    eligible_candidates: list[ExportCandidate],
    by_family: dict[str, list[ExportCandidate]],
    selected_lines_by_file: dict[str, list[str]],
    selected_unique_candidates: int,
    status_counts: dict[str, int],
    speed_quality_summary: dict[str, object],
    multihost_quality_summary: dict[str, object],
    fallback_used: bool,
    fallback_reason: str | None,
) -> dict[str, Any]:
    output_limits = {
        BLACK_FILE: settings.EXPORT_BLACK_LIMIT,
        WHITE_CIDR_FILE: settings.EXPORT_WHITE_CIDR_LIMIT,
        WHITE_SNI_FILE: settings.EXPORT_WHITE_SNI_LIMIT,
        ALL_FILE: settings.EXPORT_ALL_LIMIT,
    }
    considered_by_file = {
        BLACK_FILE: len(by_family[SourceFamily.BLACK.value]),
        WHITE_CIDR_FILE: len(by_family[SourceFamily.WHITE_CIDR.value]),
        WHITE_SNI_FILE: len(by_family[SourceFamily.WHITE_SNI.value]),
        ALL_FILE: len(eligible_candidates),
    }

    output_files = {
        file_name: {
            "considered": considered_by_file[file_name],
            "selected": len(selected_lines_by_file[file_name]),
            "limit": output_limits[file_name],
        }
        for file_name in _OUTPUT_FILES
    }

    return {
        "generated_at": generated_at.isoformat(),
        "active_count": active_count,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "source": {
            "ranking_source": "proxy_state",
            "config_source": "proxy_candidates",
            "eligibility_source": "exporter_hard_policy",
            "status_filter": ProxyStatus.ACTIVE.value,
            "requires_positive_final_score": True,
            "requires_non_empty_raw_config": True,
            "requires_enabled_candidates": True,
            "fallback_policy": "reuse_last_good_when_active_empty",
            "geo_is_diagnostic_only": True,
        },
        "sorting": [
            "final_score DESC",
            "stability_ratio DESC NULLS LAST",
            "last_success_at DESC NULLS LAST",
            "candidate_id ASC",
        ],
        "diversity_limits": {
            "max_per_country": export_policy.max_per_country,
            "max_per_host": export_policy.max_per_host,
            "null_country_group": _UNKNOWN_COUNTRY_GROUP,
            "null_host_fallback": "fingerprint then candidate_id",
        },
        "hardening_policy": _export_policy_payload(export_policy),
        "limits": {
            "EXPORT_BLACK_LIMIT": settings.EXPORT_BLACK_LIMIT,
            "EXPORT_WHITE_CIDR_LIMIT": settings.EXPORT_WHITE_CIDR_LIMIT,
            "EXPORT_WHITE_SNI_LIMIT": settings.EXPORT_WHITE_SNI_LIMIT,
            "EXPORT_ALL_LIMIT": settings.EXPORT_ALL_LIMIT,
        },
        "considered_candidates_total": len(eligible_candidates),
        "considered_by_family": {
            SourceFamily.BLACK.value: len(by_family[SourceFamily.BLACK.value]),
            SourceFamily.WHITE_CIDR.value: len(by_family[SourceFamily.WHITE_CIDR.value]),
            SourceFamily.WHITE_SNI.value: len(by_family[SourceFamily.WHITE_SNI.value]),
        },
        "proxy_state_status_counts": status_counts,
        "speed_quality": speed_quality_summary,
        "multihost_quality": multihost_quality_summary,
        "selected_candidates_total_across_files": sum(len(items) for items in selected_lines_by_file.values()),
        "selected_unique_candidates": selected_unique_candidates,
        "output_files": output_files,
    }
