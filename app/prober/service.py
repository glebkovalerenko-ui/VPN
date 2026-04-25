"""Stage 7 prober orchestration: select, probe, geo-enrich, persist checks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import session_scope
from app.common.logging import get_logger
from app.common.settings import Settings, get_settings
from app.geo.models import GeoLookupResult
from app.geo.service import GeoService, compute_geo_match

from .checker import ProbeResult, SingBoxProbeBackend
from .errors import ProbeErrorCode
from .selectors import select_candidates_for_probe

logger = get_logger(__name__)


@dataclass(slots=True)
class ProbeCycleStats:
    """Execution metrics for one probe run."""

    selected: int = 0
    succeeded: int = 0
    failed: int = 0
    unsupported: int = 0
    speed_measured: int = 0
    speed_unavailable: int = 0
    speed_failure_reasons: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.speed_failure_reasons is None:
            self.speed_failure_reasons = Counter()

    def to_log_extra(self) -> dict[str, int | dict[str, int]]:
        speed_failure_reasons = self.speed_failure_reasons or Counter()
        return {
            "selected": self.selected,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "unsupported": self.unsupported,
            "connect_ok": self.succeeded,
            "speed_measured": self.speed_measured,
            "speed_unavailable": self.speed_unavailable,
            "speed_failure_reasons": dict(sorted(speed_failure_reasons.items())),
        }


def run_probe_cycle(app_settings: Settings | None = None) -> ProbeCycleStats:
    """Run one Stage 7 probe cycle with deterministic selection."""
    settings = app_settings or get_settings()
    stats = ProbeCycleStats()

    with session_scope(settings) as session:
        candidates = select_candidates_for_probe(
            session,
            batch_size=settings.PROBE_BATCH_SIZE,
        )

    stats.selected = len(candidates)
    logger.info(
        "Prober batch started",
        extra={
            "batch_size_limit": settings.PROBE_BATCH_SIZE,
            "selected": stats.selected,
            "connect_timeout_seconds": settings.CONNECT_TIMEOUT_SECONDS,
            "read_timeout_seconds": settings.DOWNLOAD_TIMEOUT_SECONDS,
            "singbox_binary": settings.SINGBOX_BINARY,
            "process_start_timeout_seconds": settings.PROBER_PROCESS_START_TIMEOUT_SECONDS,
            "geo_provider_primary": settings.GEO_PROVIDER_PRIMARY,
            "geo_provider_fallback": settings.GEO_PROVIDER_FALLBACK,
            "geo_request_timeout_seconds": settings.GEO_REQUEST_TIMEOUT_SECONDS,
            "speed_test_urls": settings.speed_test_urls,
            "speed_test_attempts": settings.SPEED_TEST_ATTEMPTS,
            "speed_test_timeout": settings.speed_test_timeout,
            "speed_test_max_bytes": settings.SPEED_TEST_MAX_BYTES,
            "speed_test_chunk_size": settings.SPEED_TEST_CHUNK_SIZE,
        },
    )

    backend = SingBoxProbeBackend(
        singbox_binary=settings.SINGBOX_BINARY,
        bind_host=settings.PROBER_LOCAL_BIND_HOST,
        base_local_port=settings.PROBER_BASE_LOCAL_PORT,
        process_start_timeout_seconds=settings.PROBER_PROCESS_START_TIMEOUT_SECONDS,
        temp_dir=settings.PROBER_TEMP_DIR,
        connect_timeout_seconds=settings.CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds=settings.DOWNLOAD_TIMEOUT_SECONDS,
        exit_ip_url=settings.PROBER_EXIT_IP_URL,
        speed_test_urls=settings.speed_test_urls,
        speed_test_attempts=settings.SPEED_TEST_ATTEMPTS,
        speed_test_timeout=settings.speed_test_timeout,
        speed_test_max_bytes=settings.SPEED_TEST_MAX_BYTES,
        speed_test_chunk_size=settings.SPEED_TEST_CHUNK_SIZE,
    )
    geo_service = GeoService(settings)

    for candidate in candidates:
        result = backend.probe_candidate(candidate)
        exit_country, geo_match, geo_lookup_result = _resolve_geo_enrichment(
            geo_service,
            result=result,
            source_country_tag=candidate.source_country_tag,
        )

        if geo_lookup_result is not None and not geo_lookup_result.success:
            logger.warning(
                "Geo lookup failed",
                extra={
                    "candidate_id": candidate.id,
                    "protocol": candidate.protocol,
                    "exit_ip": result.exit_ip,
                    "source_country_tag": candidate.source_country_tag,
                    "provider_name": geo_lookup_result.provider_name,
                    "geo_error_code": geo_lookup_result.error_code,
                    "geo_error_text": geo_lookup_result.error_text,
                },
            )

        try:
            with session_scope(settings) as session:
                insert_proxy_check(
                    session,
                    candidate_id=candidate.id,
                    result=result,
                    exit_country=exit_country,
                    geo_match=geo_match,
                )
        except Exception:
            logger.exception(
                "Failed to persist probe result",
                extra={
                    "candidate_id": candidate.id,
                    "protocol": candidate.protocol,
                    "connect_ok": result.connect_ok,
                    "error_code": result.error_code,
                },
            )
            stats.failed += 1
            continue

        _apply_result_to_stats(stats, result)

        logger.info(
            "Candidate probed",
            extra={
                "candidate_id": candidate.id,
                "protocol": candidate.protocol,
                "connect_ok": result.connect_ok,
                "connect_ms": result.connect_ms,
                "first_byte_ms": result.first_byte_ms,
                "download_mbps": str(result.download_mbps) if result.download_mbps is not None else None,
                "speed_error_code": result.speed_error_code,
                "speed_failure_reason": result.speed_failure_reason,
                "speed_error_text": result.speed_error_text,
                "speed_endpoint_url": result.speed_endpoint_url,
                "speed_attempts": result.speed_attempts,
                "speed_successes": result.speed_successes,
                "exit_ip": result.exit_ip,
                "exit_country": exit_country,
                "geo_match": geo_match,
                "geo_provider": geo_lookup_result.provider_name if geo_lookup_result else None,
                "error_code": result.error_code,
            },
        )

    logger.info("Prober batch finished", extra=stats.to_log_extra())
    return stats


def insert_proxy_check(
    session: Session,
    *,
    candidate_id: str,
    result: ProbeResult,
    exit_country: str | None,
    geo_match: bool | None,
) -> None:
    """Persist single probe attempt into proxy_checks."""
    session.execute(
        text(
            """
            INSERT INTO proxy_checks (
                candidate_id,
                checked_at,
                connect_ok,
                connect_ms,
                first_byte_ms,
                download_mbps,
                speed_error_code,
                speed_failure_reason,
                speed_error_text,
                speed_endpoint_url,
                speed_attempts,
                speed_successes,
                exit_ip,
                exit_country,
                geo_match,
                error_code,
                error_text
            )
            VALUES (
                :candidate_id,
                :checked_at,
                :connect_ok,
                :connect_ms,
                :first_byte_ms,
                :download_mbps,
                :speed_error_code,
                :speed_failure_reason,
                :speed_error_text,
                :speed_endpoint_url,
                :speed_attempts,
                :speed_successes,
                :exit_ip,
                :exit_country,
                :geo_match,
                :error_code,
                :error_text
            )
            """
        ),
        {
            "candidate_id": candidate_id,
            "checked_at": result.checked_at,
            "connect_ok": result.connect_ok,
            "connect_ms": result.connect_ms,
            "first_byte_ms": result.first_byte_ms,
            "download_mbps": result.download_mbps,
            "speed_error_code": result.speed_error_code,
            "speed_failure_reason": result.speed_failure_reason,
            "speed_error_text": result.speed_error_text,
            "speed_endpoint_url": result.speed_endpoint_url,
            "speed_attempts": result.speed_attempts,
            "speed_successes": result.speed_successes,
            "exit_ip": result.exit_ip,
            "exit_country": exit_country,
            "geo_match": geo_match,
            "error_code": result.error_code,
            "error_text": result.error_text,
        },
    )


def _apply_result_to_stats(stats: ProbeCycleStats, result: ProbeResult) -> None:
    if result.connect_ok:
        stats.succeeded += 1
        if result.download_mbps is not None:
            stats.speed_measured += 1
        else:
            stats.speed_unavailable += 1
            reason = result.speed_failure_reason or result.speed_error_code or "speed_not_run"
            if stats.speed_failure_reasons is not None:
                stats.speed_failure_reasons[reason] += 1
        return

    stats.failed += 1
    if result.error_code == ProbeErrorCode.UNSUPPORTED_PROTOCOL.value:
        stats.unsupported += 1


def _resolve_geo_enrichment(
    geo_service: GeoService,
    *,
    result: ProbeResult,
    source_country_tag: str | None,
) -> tuple[str | None, bool | None, GeoLookupResult | None]:
    if not result.connect_ok or not result.exit_ip:
        return None, None, None

    geo_lookup = geo_service.resolve_country(result.exit_ip)
    if not geo_lookup.success:
        return None, None, geo_lookup

    exit_country = geo_lookup.country_code
    if exit_country is None:
        return None, None, geo_lookup

    return exit_country, compute_geo_match(source_country_tag, exit_country), geo_lookup
