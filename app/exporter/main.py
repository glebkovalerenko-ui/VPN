"""CLI entrypoint for Stage 9 exporter."""

from __future__ import annotations

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .service import run_export_cycle


def main() -> int:
    """Run one exporter cycle from CLI."""
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info(
        "Starting exporter CLI",
        extra={
            "export_black_limit": settings.EXPORT_BLACK_LIMIT,
            "export_white_cidr_limit": settings.EXPORT_WHITE_CIDR_LIMIT,
            "export_white_sni_limit": settings.EXPORT_WHITE_SNI_LIMIT,
            "export_all_limit": settings.EXPORT_ALL_LIMIT,
            "export_max_per_country": settings.EXPORT_MAX_PER_COUNTRY,
            "export_max_per_host": settings.EXPORT_MAX_PER_HOST,
            "export_max_latency_ms": settings.EXPORT_MAX_LATENCY_MS,
            "export_max_first_byte_ms": settings.EXPORT_MAX_FIRST_BYTE_MS,
            "export_min_download_mbps": settings.EXPORT_MIN_DOWNLOAD_MBPS,
            "export_require_speed_measurement": settings.EXPORT_REQUIRE_SPEED_MEASUREMENT,
            "export_allow_legacy_speed_if_other_signals_strong": settings.EXPORT_ALLOW_LEGACY_SPEED_IF_OTHER_SIGNALS_STRONG,
            "export_require_latest_check_success": settings.EXPORT_REQUIRE_LATEST_CHECK_SUCCESS,
            "export_max_latest_check_age_minutes": settings.EXPORT_MAX_LATEST_CHECK_AGE_MINUTES,
            "export_require_last_two_successes": settings.EXPORT_REQUIRE_LAST_TWO_SUCCESSES,
            "export_require_consecutive_successes": settings.EXPORT_REQUIRE_CONSECUTIVE_SUCCESSES,
            "export_min_consecutive_successes": settings.EXPORT_MIN_CONSECUTIVE_SUCCESSES,
            "export_recent_checks_window": settings.EXPORT_RECENT_CHECKS_WINDOW,
            "export_min_recent_success_ratio": settings.EXPORT_MIN_RECENT_SUCCESS_RATIO,
            "export_min_user_target_success_ratio": settings.EXPORT_MIN_USER_TARGET_SUCCESS_RATIO,
            "export_require_critical_targets_all_success": settings.EXPORT_REQUIRE_CRITICAL_TARGETS_ALL_SUCCESS,
            "export_min_critical_target_success_ratio": settings.EXPORT_MIN_CRITICAL_TARGET_SUCCESS_RATIO,
            "export_min_freshness_score": settings.EXPORT_MIN_FRESHNESS_SCORE,
        },
    )
    stats = run_export_cycle(settings)
    logger.info("Exporter CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
