"""File writing helpers for Stage 9 exporter."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_txt_atomic(path: Path, lines: list[str]) -> None:
    """Write TXT output atomically while preserving line order."""
    payload = "\n".join(lines)
    if lines:
        payload = f"{payload}\n"
    _write_text_atomic(path, payload)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON output atomically with stable formatting."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    _write_text_atomic(path, f"{serialized}\n")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
