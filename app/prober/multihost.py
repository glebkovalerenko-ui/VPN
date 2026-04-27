"""Lightweight multi-host verification for proxy prober."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from time import perf_counter
from typing import Any

import requests

_RATIO_SCALE = Decimal("0.0001")
_MULTIHOST_USER_AGENT = "proxy-mvp-stage12-multihost/1.0"
_NO_BODY_STATUS_CODES = {204, 205, 304}


@dataclass(slots=True, frozen=True)
class TargetProbeResult:
    """Single lightweight HTTP target check result."""

    group: str
    url: str
    success: bool
    status_code: int | None
    first_byte_ms: int | None
    latency_ms: int | None
    failure_reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "url": self.url,
            "success": self.success,
            "status_code": self.status_code,
            "first_byte_ms": self.first_byte_ms,
            "latency_ms": self.latency_ms,
            "failure_reason": self.failure_reason,
        }


@dataclass(slots=True, frozen=True)
class MultiHostMeasurement:
    """Aggregated multi-host validation metrics used by exporter hard policy."""

    enabled: bool
    user_targets_total: int
    user_targets_successful: int
    user_targets_success_ratio: Decimal | None
    critical_targets_total: int
    critical_targets_successful: int
    critical_targets_all_success: bool
    failure_reason: str | None
    passed_policy: bool
    target_results: tuple[TargetProbeResult, ...]

    def to_summary_json(
        self,
        *,
        min_user_target_success_ratio: float,
        require_critical_targets_all_success: bool,
        min_critical_target_success_ratio: float,
        max_target_first_byte_ms: int,
        max_target_latency_ms: int,
    ) -> dict[str, Any]:
        baseline_total = sum(1 for item in self.target_results if item.group == "baseline")
        baseline_successful = sum(
            1 for item in self.target_results if item.group == "baseline" and item.success
        )
        critical_total = self.critical_targets_total
        critical_successful = self.critical_targets_successful
        critical_ratio = _ratio(critical_successful, critical_total)
        return {
            "enabled": self.enabled,
            "passed_policy": self.passed_policy,
            "failure_reason": self.failure_reason,
            "policy": {
                "min_user_target_success_ratio": min_user_target_success_ratio,
                "require_critical_targets_all_success": require_critical_targets_all_success,
                "min_critical_target_success_ratio": min_critical_target_success_ratio,
                "max_target_first_byte_ms": max_target_first_byte_ms,
                "max_target_latency_ms": max_target_latency_ms,
            },
            "groups": {
                "baseline": {
                    "total": baseline_total,
                    "successful": baseline_successful,
                },
                "critical": {
                    "total": critical_total,
                    "successful": critical_successful,
                    "success_ratio": _decimal_to_float(critical_ratio),
                },
            },
            "user_targets": {
                "total": self.user_targets_total,
                "successful": self.user_targets_successful,
                "success_ratio": _decimal_to_float(self.user_targets_success_ratio),
            },
            "critical_targets": {
                "total": critical_total,
                "successful": critical_successful,
                "all_success": self.critical_targets_all_success,
            },
            "targets": [item.to_json() for item in self.target_results],
        }


def run_multihost_measurement(
    *,
    session: requests.Session,
    proxies: dict[str, str],
    timeout: tuple[int, int],
    baseline_urls: tuple[str, ...],
    critical_urls: tuple[str, ...],
    max_target_first_byte_ms: int,
    max_target_latency_ms: int,
    min_user_target_success_ratio: float,
    require_critical_targets_all_success: bool,
    min_critical_target_success_ratio: float,
    enabled: bool,
) -> MultiHostMeasurement:
    """Run bounded lightweight checks against baseline and critical host groups."""
    if not enabled:
        return MultiHostMeasurement(
            enabled=False,
            user_targets_total=0,
            user_targets_successful=0,
            user_targets_success_ratio=None,
            critical_targets_total=0,
            critical_targets_successful=0,
            critical_targets_all_success=True,
            failure_reason=None,
            passed_policy=True,
            target_results=(),
        )

    results: list[TargetProbeResult] = []
    for url in baseline_urls:
        results.append(
            run_single_target_probe(
                session=session,
                url=url,
                group="baseline",
                proxies=proxies,
                timeout=timeout,
                max_target_first_byte_ms=max_target_first_byte_ms,
                max_target_latency_ms=max_target_latency_ms,
            )
        )
    for url in critical_urls:
        results.append(
            run_single_target_probe(
                session=session,
                url=url,
                group="critical",
                proxies=proxies,
                timeout=timeout,
                max_target_first_byte_ms=max_target_first_byte_ms,
                max_target_latency_ms=max_target_latency_ms,
            )
        )

    user_targets_total = len(results)
    user_targets_successful = sum(1 for item in results if item.success)
    user_targets_success_ratio = _ratio(user_targets_successful, user_targets_total)

    critical_results = [item for item in results if item.group == "critical"]
    critical_targets_total = len(critical_results)
    critical_targets_successful = sum(1 for item in critical_results if item.success)
    critical_targets_success_ratio = _ratio(critical_targets_successful, critical_targets_total)
    critical_targets_all_success = critical_targets_total == 0 or (
        critical_targets_successful == critical_targets_total
    )

    user_ratio_ok = (
        user_targets_total > 0
        and user_targets_success_ratio is not None
        and user_targets_success_ratio >= Decimal(str(min_user_target_success_ratio))
    )
    critical_policy_ok = True
    if critical_targets_total > 0:
        if require_critical_targets_all_success:
            critical_policy_ok = critical_targets_all_success
        else:
            critical_policy_ok = (
                critical_targets_success_ratio is not None
                and critical_targets_success_ratio >= Decimal(str(min_critical_target_success_ratio))
            )

    passed_policy = user_ratio_ok and critical_policy_ok
    failure_reason = _build_failure_reason(
        user_targets_total=user_targets_total,
        user_targets_success_ratio=user_targets_success_ratio,
        min_user_target_success_ratio=min_user_target_success_ratio,
        critical_targets_total=critical_targets_total,
        critical_targets_all_success=critical_targets_all_success,
        require_critical_targets_all_success=require_critical_targets_all_success,
        critical_targets_success_ratio=critical_targets_success_ratio,
        min_critical_target_success_ratio=min_critical_target_success_ratio,
    )

    return MultiHostMeasurement(
        enabled=True,
        user_targets_total=user_targets_total,
        user_targets_successful=user_targets_successful,
        user_targets_success_ratio=user_targets_success_ratio,
        critical_targets_total=critical_targets_total,
        critical_targets_successful=critical_targets_successful,
        critical_targets_all_success=critical_targets_all_success,
        failure_reason=failure_reason,
        passed_policy=passed_policy,
        target_results=tuple(results),
    )


def run_single_target_probe(
    *,
    session: requests.Session,
    url: str,
    group: str,
    proxies: dict[str, str],
    timeout: tuple[int, int],
    max_target_first_byte_ms: int,
    max_target_latency_ms: int,
) -> TargetProbeResult:
    """Run lightweight target check: status + ttfb + bounded latency."""
    started_at = perf_counter()
    try:
        with session.get(
            url,
            proxies=proxies,
            timeout=timeout,
            headers={
                "User-Agent": _MULTIHOST_USER_AGENT,
                "Accept-Encoding": "identity",
            },
            stream=True,
            allow_redirects=True,
        ) as response:
            status_code = int(response.status_code)
            first_byte_ms = max(1, int((perf_counter() - started_at) * 1000))
            if status_code not in _NO_BODY_STATUS_CODES:
                for chunk in response.iter_content(chunk_size=1):
                    if chunk:
                        first_byte_ms = max(1, int((perf_counter() - started_at) * 1000))
                        break
            latency_ms = max(first_byte_ms, int((perf_counter() - started_at) * 1000))
            failure_reason = _target_policy_failure_reason(
                status_code=status_code,
                first_byte_ms=first_byte_ms,
                latency_ms=latency_ms,
                max_target_first_byte_ms=max_target_first_byte_ms,
                max_target_latency_ms=max_target_latency_ms,
            )
            return TargetProbeResult(
                group=group,
                url=url,
                success=failure_reason is None,
                status_code=status_code,
                first_byte_ms=first_byte_ms,
                latency_ms=latency_ms,
                failure_reason=failure_reason,
            )
    except requests.RequestException as exc:
        return TargetProbeResult(
            group=group,
            url=url,
            success=False,
            status_code=None,
            first_byte_ms=None,
            latency_ms=max(1, int((perf_counter() - started_at) * 1000)),
            failure_reason=_classify_target_request_exception(exc),
        )
    except Exception:
        return TargetProbeResult(
            group=group,
            url=url,
            success=False,
            status_code=None,
            first_byte_ms=None,
            latency_ms=max(1, int((perf_counter() - started_at) * 1000)),
            failure_reason="target_unexpected_error",
        )


def _target_policy_failure_reason(
    *,
    status_code: int,
    first_byte_ms: int,
    latency_ms: int,
    max_target_first_byte_ms: int,
    max_target_latency_ms: int,
) -> str | None:
    if status_code >= 400:
        return f"http_status_{status_code}"
    if first_byte_ms > max_target_first_byte_ms:
        return "target_high_first_byte"
    if latency_ms > max_target_latency_ms:
        return "target_high_latency"
    return None


def _classify_target_request_exception(exc: requests.RequestException) -> str:
    message = str(exc).lower()
    if isinstance(exc, requests.Timeout) or "timed out" in message or "timeout" in message:
        return "target_timeout"
    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in message or "certificate" in message:
        return "target_tls_error"
    return "target_request_error"


def _build_failure_reason(
    *,
    user_targets_total: int,
    user_targets_success_ratio: Decimal | None,
    min_user_target_success_ratio: float,
    critical_targets_total: int,
    critical_targets_all_success: bool,
    require_critical_targets_all_success: bool,
    critical_targets_success_ratio: Decimal | None,
    min_critical_target_success_ratio: float,
) -> str | None:
    if user_targets_total <= 0:
        return "no_user_targets_configured"

    if (
        user_targets_success_ratio is None
        or user_targets_success_ratio < Decimal(str(min_user_target_success_ratio))
    ):
        return "low_user_target_success_ratio"

    if critical_targets_total <= 0:
        return None
    if require_critical_targets_all_success:
        return None if critical_targets_all_success else "critical_targets_failed"
    if (
        critical_targets_success_ratio is None
        or critical_targets_success_ratio < Decimal(str(min_critical_target_success_ratio))
    ):
        return "low_critical_target_success_ratio"
    return None


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        _RATIO_SCALE,
        rounding=ROUND_HALF_UP,
    )


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)

