"""HTTP speed test helpers for Stage 7 prober."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from time import perf_counter

import requests


@dataclass(slots=True, frozen=True)
class SpeedTestResult:
    """Measured download metrics for one speed test HTTP request."""

    bytes_read: int
    first_byte_ms: int
    download_mbps: Decimal


def run_speed_test(
    *,
    session: requests.Session,
    url: str,
    proxies: dict[str, str],
    timeout: tuple[int, int],
    max_bytes: int,
    chunk_size: int,
    user_agent: str,
) -> SpeedTestResult:
    """Run bounded HTTP download through runtime proxy and compute speed metrics."""
    if max_bytes <= 0:
        raise ValueError("speed test max_bytes must be positive")
    if chunk_size <= 0:
        raise ValueError("speed test chunk_size must be positive")

    request_started_at = perf_counter()
    first_byte_at: float | None = None
    bytes_read = 0

    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "identity",
    }

    with session.get(
        url,
        proxies=proxies,
        timeout=timeout,
        headers=headers,
        stream=True,
    ) as response:
        response.raise_for_status()

        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue

            if first_byte_at is None:
                first_byte_at = perf_counter()

            remaining = max_bytes - bytes_read
            if remaining <= 0:
                break

            bytes_read += min(len(chunk), remaining)
            if bytes_read >= max_bytes:
                break

    if first_byte_at is None:
        raise ValueError("speed test response had no body bytes")
    if bytes_read <= 0:
        raise ValueError("speed test bytes_read must be positive")

    read_duration_seconds = perf_counter() - first_byte_at
    if read_duration_seconds <= 0:
        raise ValueError("speed test read duration is non-positive")

    first_byte_ms = max(1, int((first_byte_at - request_started_at) * 1000))
    download_mbps = (
        (
            (Decimal(bytes_read) * Decimal(8))
            / Decimal(str(read_duration_seconds))
            / Decimal(1_000_000)
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    )

    return SpeedTestResult(
        bytes_read=bytes_read,
        first_byte_ms=first_byte_ms,
        download_mbps=download_mbps,
    )
