"""CLI entrypoint for Stage 7 prober."""

from __future__ import annotations

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .service import run_probe_cycle


def main() -> int:
    """Run one probe cycle from CLI."""
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info(
        "Starting prober CLI",
        extra={
            "batch_size": settings.PROBE_BATCH_SIZE,
            "connect_timeout_seconds": settings.CONNECT_TIMEOUT_SECONDS,
            "read_timeout_seconds": settings.DOWNLOAD_TIMEOUT_SECONDS,
            "singbox_binary": settings.SINGBOX_BINARY,
            "speed_test_url": settings.SPEED_TEST_URL,
            "speed_test_max_bytes": settings.SPEED_TEST_MAX_BYTES,
            "speed_test_chunk_size": settings.SPEED_TEST_CHUNK_SIZE,
        },
    )
    stats = run_probe_cycle(settings)
    logger.info("Prober CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
