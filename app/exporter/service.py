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
    SelectedExportItem,
)
from .selectors import (
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


def run_export_cycle(app_settings: Settings | None = None) -> ExportCycleStats:
    """Run one Stage 9 export cycle using proxy_state as ranking source of truth."""
    settings = app_settings or get_settings()
    generated_at = datetime.now(timezone.utc)
    stats = ExportCycleStats()

    with session_scope(settings) as session:
        eligible_candidates = select_export_candidates(
            session,
            status=ProxyStatus.ACTIVE,
        )
        status_counts = fetch_proxy_state_status_counts(session)
        speed_quality_summary = fetch_speed_quality_summary(session)

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
            max_per_country=settings.MAX_PER_COUNTRY,
            max_per_host=settings.MAX_PER_HOST,
        ),
        WHITE_CIDR_FILE: _apply_diversity_limits(
            by_family[SourceFamily.WHITE_CIDR.value],
            limit=settings.EXPORT_WHITE_CIDR_LIMIT,
            max_per_country=settings.MAX_PER_COUNTRY,
            max_per_host=settings.MAX_PER_HOST,
        ),
        WHITE_SNI_FILE: _apply_diversity_limits(
            by_family[SourceFamily.WHITE_SNI.value],
            limit=settings.EXPORT_WHITE_SNI_LIMIT,
            max_per_country=settings.MAX_PER_COUNTRY,
            max_per_host=settings.MAX_PER_HOST,
        ),
        ALL_FILE: _apply_diversity_limits(
            eligible_candidates,
            limit=settings.EXPORT_ALL_LIMIT,
            max_per_country=settings.MAX_PER_COUNTRY,
            max_per_host=settings.MAX_PER_HOST,
        ),
    }

    output_dir = PROJECT_ROOT / "output"
    selected_lines_by_file = {
        file_name: [candidate.raw_config for candidate in selection_result.selected_candidates]
        for file_name, selection_result in selection_results.items()
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
            selection_result=selection_result,
            fallback_used=fallback.use_fallback,
            fallback_reason=fallback.reason,
            exported_lines_count=len(selected_lines_by_file[export_name]),
            speed_quality_summary=speed_quality_summary,
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
        active_count=len(eligible_candidates),
        eligible_candidates=eligible_candidates,
        by_family=by_family,
        selected_lines_by_file=selected_lines_by_file,
        selected_unique_candidates=selected_unique_candidates,
        status_counts=status_counts,
        speed_quality_summary=speed_quality_summary,
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


def _apply_diversity_limits(
    ordered_candidates: list[ExportCandidate],
    *,
    limit: int,
    max_per_country: int,
    max_per_host: int,
) -> ExportSelectionResult:
    selected: list[ExportCandidate] = []
    selected_items: list[SelectedExportItem] = []
    seen_raw_configs: set[str] = set()
    country_counts: Counter[str] = Counter()
    host_counts: Counter[str] = Counter()
    summary = ExportSelectionSummary(
        considered=len(ordered_candidates),
        selected=0,
        limit=limit,
        max_per_country=max_per_country,
        max_per_host=max_per_host,
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

        raw_config = candidate.raw_config.strip()
        if not raw_config:
            summary.empty_or_invalid_skipped += 1
            continue
        if raw_config in seen_raw_configs:
            summary.dedup_raw_config_skipped += 1
            continue
        if "\n" in raw_config or "\r" in raw_config:
            summary.empty_or_invalid_skipped += 1
            continue

        summary.eligible_before_diversity += 1
        country_group = _country_group(candidate.current_country)
        if country_counts[country_group] >= max_per_country:
            summary.country_limit_skipped += 1
            continue

        host_group = _host_group(candidate)
        if host_counts[host_group] >= max_per_host:
            summary.host_limit_skipped += 1
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
        summary=summary,
    )


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
    selection_result: ExportSelectionResult,
    fallback_used: bool,
    fallback_reason: str | None,
    exported_lines_count: int,
    speed_quality_summary: dict[str, object],
) -> dict[str, Any]:
    summary = selection_result.summary
    summary_payload: dict[str, int] = {
        "considered": summary.considered,
        "selected": summary.selected,
        "limit": summary.limit,
        "max_per_country": summary.max_per_country,
        "max_per_host": summary.max_per_host,
        "dedup_raw_config_skipped": summary.dedup_raw_config_skipped,
        "country_limit_skipped": summary.country_limit_skipped,
        "host_limit_skipped": summary.host_limit_skipped,
        "empty_or_invalid_skipped": summary.empty_or_invalid_skipped,
        "eligible_before_diversity": summary.eligible_before_diversity,
        "selected_after_diversity": summary.selected_after_diversity,
    }
    if fallback_used:
        summary_payload["exported_lines_count"] = exported_lines_count

    items = [
        {
            "selection_position": item.selection_position,
            "candidate_id": item.candidate.candidate_id,
            "family": item.candidate.family,
            "status": item.candidate.status,
            "raw_config": item.candidate.raw_config,
            "host": item.candidate.host,
            "fingerprint": item.candidate.fingerprint,
            "current_country": item.candidate.current_country,
            "selection_country_group": item.selection_country_group,
            "selection_host_group": item.selection_host_group,
            "final_score": _decimal_to_json_number(item.candidate.final_score),
            "stability_ratio": _decimal_to_json_number(item.candidate.stability_ratio),
            "geo_confidence": _decimal_to_json_number(item.candidate.geo_confidence),
            "freshness_score": _decimal_to_json_number(item.candidate.freshness_score),
            "latency_ms": item.candidate.latency_ms,
            "download_mbps": _decimal_to_json_number(item.candidate.download_mbps),
            "speed_diagnostics": {
                "speed_error_code": item.candidate.speed_error_code,
                "speed_failure_reason": item.candidate.speed_failure_reason,
                "speed_error_text": item.candidate.speed_error_text,
                "speed_endpoint_url": item.candidate.speed_endpoint_url,
                "speed_attempts": item.candidate.speed_attempts,
                "speed_successes": item.candidate.speed_successes,
            },
            "last_success_at": _datetime_to_iso(item.candidate.last_success_at),
            "rank_global": item.candidate.rank_global,
            "rank_in_family": item.candidate.rank_in_family,
            "rank_in_country": item.candidate.rank_in_country,
        }
        for item in selection_result.selected_items
    ]
    return {
        "generated_at": generated_at.isoformat(),
        "export_name": export_name,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "summary": summary_payload,
        "speed_quality": speed_quality_summary,
        "items": items,
    }


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
    active_count: int,
    eligible_candidates: list[ExportCandidate],
    by_family: dict[str, list[ExportCandidate]],
    selected_lines_by_file: dict[str, list[str]],
    selected_unique_candidates: int,
    status_counts: dict[str, int],
    speed_quality_summary: dict[str, object],
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
            "status_filter": ProxyStatus.ACTIVE.value,
            "requires_positive_final_score": True,
            "requires_non_empty_raw_config": True,
            "requires_enabled_candidates": True,
            "fallback_policy": "reuse_last_good_when_active_empty",
        },
        "sorting": [
            "final_score DESC",
            "stability_ratio DESC NULLS LAST",
            "last_success_at DESC NULLS LAST",
            "candidate_id ASC",
        ],
        "diversity_limits": {
            "max_per_country": settings.MAX_PER_COUNTRY,
            "max_per_host": settings.MAX_PER_HOST,
            "null_country_group": _UNKNOWN_COUNTRY_GROUP,
            "null_host_fallback": "fingerprint then candidate_id",
        },
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
        "selected_candidates_total_across_files": sum(len(items) for items in selected_lines_by_file.values()),
        "selected_unique_candidates": selected_unique_candidates,
        "output_files": output_files,
    }
