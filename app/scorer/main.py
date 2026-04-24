"""CLI entrypoint for Stage 8 scorer."""

from __future__ import annotations

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .service import run_scorer_cycle


def main() -> int:
    """Run one scorer cycle from CLI."""
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info(
        "Starting scorer CLI",
        extra={
            "recent_checks_limit": settings.SCORER_RECENT_CHECKS_LIMIT,
            "min_active_stability": settings.SCORER_MIN_ACTIVE_STABILITY,
            "min_degraded_stability": settings.SCORER_MIN_DEGRADED_STABILITY,
            "latency_good_ms": settings.SCORER_LATENCY_GOOD_MS,
            "latency_bad_ms": settings.SCORER_LATENCY_BAD_MS,
            "speed_good_mbps": settings.SCORER_SPEED_GOOD_MBPS,
            "speed_bad_mbps": settings.SCORER_SPEED_BAD_MBPS,
            "geo_neutral_score": settings.SCORER_GEO_NEUTRAL_SCORE,
        },
    )
    stats = run_scorer_cycle(settings)
    logger.info("Scorer CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

