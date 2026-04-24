"""CLI entrypoint for Stage 4 parser."""

from __future__ import annotations

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .service import run_parse_cycle


def main() -> int:
    """Run parser cycle from CLI."""
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info("Starting parser CLI")
    stats = run_parse_cycle(settings)
    logger.info("Parser CLI completed", extra=stats.to_log_extra())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
