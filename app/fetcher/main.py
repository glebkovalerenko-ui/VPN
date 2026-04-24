"""CLI entrypoint for upstream source fetcher."""

from __future__ import annotations

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .service import run_fetch_cycle


def main() -> int:
    """Run one fetch cycle from CLI."""
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info(
        "Starting fetcher CLI",
        extra={"timeout_seconds": settings.DOWNLOAD_TIMEOUT_SECONDS},
    )
    stats = run_fetch_cycle(settings)
    logger.info("Fetcher CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
