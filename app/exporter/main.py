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
            "max_per_country": settings.MAX_PER_COUNTRY,
            "max_per_host": settings.MAX_PER_HOST,
        },
    )
    stats = run_export_cycle(settings)
    logger.info("Exporter CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
