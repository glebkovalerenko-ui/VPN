"""CLI smoke-check: verify database connectivity."""

from __future__ import annotations

from .db import check_db_connection
from .logging import get_logger


def main() -> int:
    logger = get_logger("app.common.cli_check_db")
    ok, details = check_db_connection()
    if ok:
        logger.info("Database connectivity check passed")
        return 0

    logger.error("Database connectivity check failed", extra={"error": details})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

