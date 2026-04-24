"""CLI smoke-check: print safe settings summary."""

from __future__ import annotations

import json

from .settings import get_settings


def main() -> int:
    settings = get_settings()
    print(json.dumps(settings.safe_summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

