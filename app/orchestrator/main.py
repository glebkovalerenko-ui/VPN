"""CLI entrypoint for Stage 11 orchestrator loop."""

from __future__ import annotations

from app.common.logging import configure_logging

from .service import run_orchestrator_loop


def main() -> int:
    configure_logging()
    return run_orchestrator_loop()


if __name__ == "__main__":
    raise SystemExit(main())

