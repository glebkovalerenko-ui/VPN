"""Stage 9 exporter orchestration service."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.common.db import session_scope
from app.common.enums import ProxyStatus, SourceFamily
from app.common.logging import get_logger
from app.common.settings import PROJECT_ROOT, Settings, get_settings

from .models import ExportCandidate
from .selectors import fetch_proxy_state_status_counts, select_export_candidates
from .writer import write_json_atomic, write_txt_atomic

logger = get_logger(__name__)

BLACK_FILE = "BLACK-ETALON.txt"
WHITE_CIDR_FILE = "WHITE-CIDR-ETALON.txt"
WHITE_SNI_FILE = "WHITE-SNI-ETALON.txt"
ALL_FILE = "ALL-ETALON.txt"
MANIFEST_FILE = "export_manifest.json"

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

    selections: dict[str, list[ExportCandidate]] = {
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
    for file_name, selected_candidates in selections.items():
        write_txt_atomic(
            output_dir / file_name,
            [candidate.raw_config for candidate in selected_candidates],
        )

    manifest = _build_manifest(
        generated_at=generated_at,
        settings=settings,
        eligible_candidates=eligible_candidates,
        by_family=by_family,
        selections=selections,
        status_counts=status_counts,
    )
    manifest_path = output_dir / MANIFEST_FILE
    write_json_atomic(manifest_path, manifest)

    selected_ids = {
        candidate.candidate_id
        for selected_candidates in selections.values()
        for candidate in selected_candidates
    }

    stats.considered_candidates = len(eligible_candidates)
    stats.selected_total_across_files = sum(len(items) for items in selections.values())
    stats.selected_unique_candidates = len(selected_ids)
    stats.black_selected = len(selections[BLACK_FILE])
    stats.white_cidr_selected = len(selections[WHITE_CIDR_FILE])
    stats.white_sni_selected = len(selections[WHITE_SNI_FILE])
    stats.all_selected = len(selections[ALL_FILE])
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
) -> list[ExportCandidate]:
    selected: list[ExportCandidate] = []
    seen_raw_configs: set[str] = set()
    country_counts: Counter[str] = Counter()
    host_counts: Counter[str] = Counter()

    for candidate in ordered_candidates:
        if len(selected) >= limit:
            break

        raw_config = candidate.raw_config.strip()
        if not raw_config or raw_config in seen_raw_configs:
            continue
        if "\n" in raw_config or "\r" in raw_config:
            continue

        country_group = _country_group(candidate.current_country)
        if country_counts[country_group] >= max_per_country:
            continue

        host_group = _host_group(candidate)
        if host_counts[host_group] >= max_per_host:
            continue

        selected.append(candidate)
        seen_raw_configs.add(raw_config)
        country_counts[country_group] += 1
        host_counts[host_group] += 1

    return selected


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


def _build_manifest(
    *,
    generated_at: datetime,
    settings: Settings,
    eligible_candidates: list[ExportCandidate],
    by_family: dict[str, list[ExportCandidate]],
    selections: dict[str, list[ExportCandidate]],
    status_counts: dict[str, int],
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

    selected_ids = {
        candidate.candidate_id
        for selected_candidates in selections.values()
        for candidate in selected_candidates
    }

    output_files = {
        file_name: {
            "considered": considered_by_file[file_name],
            "selected": len(selected_candidates),
            "limit": output_limits[file_name],
        }
        for file_name, selected_candidates in selections.items()
    }

    return {
        "generated_at": generated_at.isoformat(),
        "source": {
            "ranking_source": "proxy_state",
            "config_source": "proxy_candidates",
            "status_filter": ProxyStatus.ACTIVE.value,
            "requires_positive_final_score": True,
            "requires_non_empty_raw_config": True,
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
        "selected_candidates_total_across_files": sum(len(items) for items in selections.values()),
        "selected_unique_candidates": len(selected_ids),
        "output_files": output_files,
    }
