"""HTTP speed test helpers for Stage 7 prober."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from time import perf_counter

import requests


class SpeedFailureCode(StrEnum):
    """Stable speed measurement diagnostics persisted with proxy checks."""

    TIMEOUT = "speed_timeout"
    TLS_ERROR = "speed_tls_error"
    HTTP_ERROR = "speed_http_error"
    EMPTY_BODY = "speed_empty_body"
    INVALID_RESPONSE = "speed_invalid_response"
    ALL_ENDPOINTS_FAILED = "speed_all_endpoints_failed"
    UNEXPECTED_ERROR = "speed_unexpected_error"


@dataclass(slots=True, frozen=True)
class SpeedTestFailure(Exception):
    """Expected speed-test failure with normalized diagnostics."""

    code: SpeedFailureCode
    text: str
    first_byte_ms: int | None = None
    bytes_read: int = 0

    def __str__(self) -> str:
        return self.text


@dataclass(slots=True, frozen=True)
class SpeedTestResult:
    """Measured download metrics for one speed test HTTP request."""

    endpoint_url: str
    bytes_read: int
    first_byte_ms: int
    download_mbps: Decimal


@dataclass(slots=True, frozen=True)
class SpeedAttemptFailure:
    """Diagnostics for one failed speed-test attempt."""

    endpoint_url: str
    attempt_number: int
    code: SpeedFailureCode
    text: str
    first_byte_ms: int | None
    bytes_read: int


@dataclass(slots=True, frozen=True)
class SpeedMeasurement:
    """Aggregated speed measurement across bounded endpoint attempts."""

    first_byte_ms: int | None
    download_mbps: Decimal | None
    bytes_read: int
    endpoint_url: str | None
    attempts: int
    successes: int
    error_code: SpeedFailureCode | None = None
    failure_reason: SpeedFailureCode | None = None
    error_text: str | None = None
    attempt_failures: tuple[SpeedAttemptFailure, ...] = ()

    @property
    def success(self) -> bool:
        return self.download_mbps is not None


def run_speed_measurement(
    *,
    session: requests.Session,
    urls: tuple[str, ...],
    proxies: dict[str, str],
    timeout: tuple[int, int],
    max_bytes: int,
    chunk_size: int,
    user_agent: str,
    attempts: int,
) -> SpeedMeasurement:
    """Run deterministic bounded speed attempts and return median throughput."""
    if attempts <= 0:
        raise ValueError("speed test attempts must be positive")

    endpoints = tuple(url.strip() for url in urls if url.strip())
    if not endpoints:
        return SpeedMeasurement(
            first_byte_ms=None,
            download_mbps=None,
            bytes_read=0,
            endpoint_url=None,
            attempts=0,
            successes=0,
            error_code=SpeedFailureCode.ALL_ENDPOINTS_FAILED,
            failure_reason=SpeedFailureCode.INVALID_RESPONSE,
            error_text="No speed test endpoints configured",
        )

    successes: list[SpeedTestResult] = []
    failures: list[SpeedAttemptFailure] = []
    max_attempts = max(1, attempts, len(endpoints))
    target_successes = min(3, max_attempts)

    for attempt_index in range(max_attempts):
        if len(successes) >= target_successes:
            break

        endpoint = endpoints[attempt_index % len(endpoints)]
        attempt_number = attempt_index + 1
        try:
            successes.append(
                run_speed_test(
                    session=session,
                    url=endpoint,
                    proxies=proxies,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    chunk_size=chunk_size,
                    user_agent=user_agent,
                )
            )
        except SpeedTestFailure as exc:
            failures.append(
                SpeedAttemptFailure(
                    endpoint_url=endpoint,
                    attempt_number=attempt_number,
                    code=exc.code,
                    text=exc.text,
                    first_byte_ms=exc.first_byte_ms,
                    bytes_read=exc.bytes_read,
                )
            )
        except Exception as exc:
            failures.append(
                SpeedAttemptFailure(
                    endpoint_url=endpoint,
                    attempt_number=attempt_number,
                    code=SpeedFailureCode.UNEXPECTED_ERROR,
                    text=_short_error_text(exc),
                    first_byte_ms=None,
                    bytes_read=0,
                )
            )
            break

    if successes:
        attempts_run = len(successes) + len(failures)
        median_speed = _median_decimal([sample.download_mbps for sample in successes])
        median_first_byte = _median_int([sample.first_byte_ms for sample in successes])
        median_bytes = _median_int([sample.bytes_read for sample in successes]) or 0
        median_sample = _median_sample_by_speed(successes)
        return SpeedMeasurement(
            first_byte_ms=median_first_byte,
            download_mbps=median_speed,
            bytes_read=median_bytes,
            endpoint_url=median_sample.endpoint_url,
            attempts=attempts_run,
            successes=len(successes),
            attempt_failures=tuple(failures),
        )

    failure_reason = _primary_failure_reason(failures)
    attempts_run = len(successes) + len(failures)
    error_code = (
        SpeedFailureCode.UNEXPECTED_ERROR
        if failure_reason == SpeedFailureCode.UNEXPECTED_ERROR
        else SpeedFailureCode.ALL_ENDPOINTS_FAILED
    )
    return SpeedMeasurement(
        first_byte_ms=_median_optional_int([failure.first_byte_ms for failure in failures]),
        download_mbps=None,
        bytes_read=max((failure.bytes_read for failure in failures), default=0),
        endpoint_url=failures[-1].endpoint_url if failures else None,
        attempts=attempts_run,
        successes=0,
        error_code=error_code,
        failure_reason=failure_reason,
        error_text=_build_all_failed_text(failures),
        attempt_failures=tuple(failures),
    )


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
        raise SpeedTestFailure(
            SpeedFailureCode.INVALID_RESPONSE,
            "speed test max_bytes must be positive",
        )
    if chunk_size <= 0:
        raise SpeedTestFailure(
            SpeedFailureCode.INVALID_RESPONSE,
            "speed test chunk_size must be positive",
        )

    request_started_at = perf_counter()
    first_byte_at: float | None = None
    bytes_read = 0

    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "identity",
    }

    try:
        with session.get(
            url,
            proxies=proxies,
            timeout=timeout,
            headers=headers,
            stream=True,
        ) as response:
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise SpeedTestFailure(
                    SpeedFailureCode.HTTP_ERROR,
                    _short_error_text(exc),
                    first_byte_ms=_first_byte_ms(first_byte_at, request_started_at),
                    bytes_read=bytes_read,
                ) from exc

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
    except SpeedTestFailure:
        raise
    except requests.RequestException as exc:
        code = classify_speed_request_exception(exc)
        raise SpeedTestFailure(
            code,
            _short_error_text(exc),
            first_byte_ms=_first_byte_ms(first_byte_at, request_started_at),
            bytes_read=bytes_read,
        ) from exc

    if first_byte_at is None:
        raise SpeedTestFailure(
            SpeedFailureCode.EMPTY_BODY,
            "speed test response had no body bytes",
            first_byte_ms=None,
            bytes_read=bytes_read,
        )
    if bytes_read <= 0:
        raise SpeedTestFailure(
            SpeedFailureCode.EMPTY_BODY,
            "speed test bytes_read must be positive",
            first_byte_ms=_first_byte_ms(first_byte_at, request_started_at),
            bytes_read=bytes_read,
        )

    read_duration_seconds = perf_counter() - first_byte_at
    if read_duration_seconds <= 0:
        raise SpeedTestFailure(
            SpeedFailureCode.INVALID_RESPONSE,
            "speed test read duration is non-positive",
            first_byte_ms=_first_byte_ms(first_byte_at, request_started_at),
            bytes_read=bytes_read,
        )

    first_byte_ms = max(1, int((first_byte_at - request_started_at) * 1000))
    download_mbps = (
        (
            (Decimal(bytes_read) * Decimal(8))
            / Decimal(str(read_duration_seconds))
            / Decimal(1_000_000)
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    )

    return SpeedTestResult(
        endpoint_url=url,
        bytes_read=bytes_read,
        first_byte_ms=first_byte_ms,
        download_mbps=download_mbps,
    )


def classify_speed_request_exception(exc: requests.RequestException) -> SpeedFailureCode:
    """Map requests exceptions into speed-specific failure reasons."""
    message = str(exc).lower()
    if isinstance(exc, requests.Timeout) or "timed out" in message or "timeout" in message:
        return SpeedFailureCode.TIMEOUT
    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in message or "certificate" in message:
        return SpeedFailureCode.TLS_ERROR
    if isinstance(exc, requests.HTTPError):
        return SpeedFailureCode.HTTP_ERROR
    return SpeedFailureCode.UNEXPECTED_ERROR


def _primary_failure_reason(failures: list[SpeedAttemptFailure]) -> SpeedFailureCode | None:
    if not failures:
        return None

    counts = Counter(failure.code for failure in failures)
    return counts.most_common(1)[0][0]


def _build_all_failed_text(failures: list[SpeedAttemptFailure]) -> str:
    if not failures:
        return "All speed test endpoints failed; no attempts were recorded"

    counts = Counter(failure.code.value for failure in failures)
    reason_summary = ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
    attempts_summary = "; ".join(
        (
            f"attempt={failure.attempt_number} endpoint={failure.endpoint_url} "
            f"reason={failure.code.value} error={failure.text[:180]}"
        )
        for failure in failures
    )
    return f"All speed test endpoints failed ({reason_summary}). {attempts_summary}"[:1000]


def _median_sample_by_speed(samples: list[SpeedTestResult]) -> SpeedTestResult:
    ordered = sorted(samples, key=lambda item: item.download_mbps)
    return ordered[len(ordered) // 2]


def _median_decimal(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid].quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    average = (ordered[mid - 1] + ordered[mid]) / Decimal("2")
    return average.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None

    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]

    average = (Decimal(ordered[mid - 1]) + Decimal(ordered[mid])) / Decimal("2")
    return int(average.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _median_optional_int(values: list[int | None]) -> int | None:
    return _median_int([value for value in values if value is not None])


def _first_byte_ms(first_byte_at: float | None, request_started_at: float) -> int | None:
    if first_byte_at is None:
        return None
    return max(1, int((first_byte_at - request_started_at) * 1000))


def _short_error_text(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:500]
