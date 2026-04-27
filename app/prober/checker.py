"""Probe backend implementation for Stage 7 connect + exit-IP + speed checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
from time import perf_counter
from typing import Any

import requests

from app.common.logging import get_logger

from .config_builder import build_outbound_config, build_probe_config
from .errors import ControlledProbeError, ProbeErrorCode, classify_request_exception
from .multihost import MultiHostMeasurement, run_multihost_measurement
from .selectors import ProbeCandidate
from .singbox import SingBoxRuntime
from .speedtest import SpeedFailureCode, SpeedMeasurement, run_speed_measurement

_FALLBACK_EXIT_IP_URLS: tuple[str, ...] = (
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
)
_EXIT_IP_USER_AGENT = "proxy-mvp-stage7-prober/3.0"
_SPEED_TEST_USER_AGENT = "proxy-mvp-stage7-speedtest/1.0"

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ProbeResult:
    """Normalized output of one candidate probe attempt."""

    checked_at: datetime
    connect_ok: bool
    connect_ms: int | None
    exit_ip: str | None
    first_byte_ms: int | None = None
    download_mbps: Decimal | None = None
    speed_error_code: str | None = None
    speed_failure_reason: str | None = None
    speed_error_text: str | None = None
    speed_endpoint_url: str | None = None
    speed_attempts: int = 0
    speed_successes: int = 0
    user_targets_total: int = 0
    user_targets_successful: int = 0
    user_targets_success_ratio: Decimal | None = None
    critical_targets_total: int = 0
    critical_targets_successful: int = 0
    critical_targets_all_success: bool = True
    multihost_failure_reason: str | None = None
    multihost_summary: dict[str, Any] | None = None
    error_code: str | None = None
    error_text: str | None = None


class ProbeBackend:
    """Base contract for probe backends."""

    def probe_candidate(self, candidate: ProbeCandidate) -> ProbeResult:
        raise NotImplementedError


class SingBoxProbeBackend(ProbeBackend):
    """Runtime backend that probes candidates through sing-box subprocess."""

    def __init__(
        self,
        *,
        singbox_binary: str,
        bind_host: str,
        base_local_port: int,
        process_start_timeout_seconds: int,
        temp_dir: str | None,
        connect_timeout_seconds: int,
        read_timeout_seconds: int,
        exit_ip_url: str,
        speed_test_urls: tuple[str, ...],
        speed_test_attempts: int,
        speed_test_timeout: tuple[int, int],
        speed_test_max_bytes: int,
        speed_test_chunk_size: int,
        multihost_enabled: bool,
        baseline_urls: tuple[str, ...],
        critical_urls: tuple[str, ...],
        min_user_target_success_ratio: float,
        require_critical_targets_all_success: bool,
        min_critical_target_success_ratio: float,
        max_target_first_byte_ms: int,
        max_target_latency_ms: int,
    ) -> None:
        self._runtime: SingBoxRuntime | None = None
        self._init_error: ControlledProbeError | None = None
        try:
            self._runtime = SingBoxRuntime(
                binary=singbox_binary,
                bind_host=bind_host,
                base_local_port=base_local_port,
                start_timeout_seconds=process_start_timeout_seconds,
                temp_dir=temp_dir,
            )
        except ControlledProbeError as exc:
            self._init_error = exc

        self._timeout = (connect_timeout_seconds, read_timeout_seconds)
        self._session = requests.Session()
        self._exit_ip_urls = self._build_exit_ip_urls(exit_ip_url)
        self._speed_test_urls = tuple(url.strip() for url in speed_test_urls if url.strip())
        self._speed_test_attempts = speed_test_attempts
        self._speed_test_timeout = speed_test_timeout
        self._speed_test_max_bytes = speed_test_max_bytes
        self._speed_test_chunk_size = speed_test_chunk_size
        self._multihost_enabled = multihost_enabled
        self._baseline_urls = tuple(url.strip() for url in baseline_urls if url.strip())
        self._critical_urls = tuple(url.strip() for url in critical_urls if url.strip())
        self._min_user_target_success_ratio = min_user_target_success_ratio
        self._require_critical_targets_all_success = require_critical_targets_all_success
        self._min_critical_target_success_ratio = min_critical_target_success_ratio
        self._max_target_first_byte_ms = max_target_first_byte_ms
        self._max_target_latency_ms = max_target_latency_ms

    def probe_candidate(self, candidate: ProbeCandidate) -> ProbeResult:
        checked_at = datetime.now(timezone.utc)
        started_at = perf_counter()

        try:
            if self._init_error is not None:
                raise self._init_error
            if self._runtime is None:
                raise ControlledProbeError(
                    code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
                    text="sing-box runtime is not initialized",
                )

            outbound = build_outbound_config(candidate)
            listen_port = self._runtime.allocate_port()
            config = build_probe_config(
                outbound=outbound,
                listen_host=self._runtime.bind_host,
                listen_port=listen_port,
            )

            with self._runtime.run(config=config, listen_port=listen_port):
                proxies = self._build_runtime_proxies(listen_port)
                exit_ip = self._resolve_exit_ip(proxies)
                connect_ms = max(1, int((perf_counter() - started_at) * 1000))
                multihost_result = self._run_multihost_measurement(proxies=proxies)
                speed_result = self._try_speed_test(
                    candidate_id=candidate.id,
                    listen_port=listen_port,
                    proxies=proxies,
                )

            multihost_summary = self._build_multihost_summary(
                multihost_result=multihost_result,
                speed_result=speed_result,
            )

            return ProbeResult(
                checked_at=checked_at,
                connect_ok=True,
                connect_ms=connect_ms,
                exit_ip=exit_ip,
                first_byte_ms=speed_result.first_byte_ms if speed_result else None,
                download_mbps=speed_result.download_mbps if speed_result else None,
                speed_error_code=speed_result.error_code.value if speed_result and speed_result.error_code else None,
                speed_failure_reason=(
                    speed_result.failure_reason.value if speed_result and speed_result.failure_reason else None
                ),
                speed_error_text=speed_result.error_text if speed_result else None,
                speed_endpoint_url=speed_result.endpoint_url if speed_result else None,
                speed_attempts=speed_result.attempts if speed_result else 0,
                speed_successes=speed_result.successes if speed_result else 0,
                user_targets_total=multihost_result.user_targets_total,
                user_targets_successful=multihost_result.user_targets_successful,
                user_targets_success_ratio=multihost_result.user_targets_success_ratio,
                critical_targets_total=multihost_result.critical_targets_total,
                critical_targets_successful=multihost_result.critical_targets_successful,
                critical_targets_all_success=multihost_result.critical_targets_all_success,
                multihost_failure_reason=multihost_result.failure_reason,
                multihost_summary=multihost_summary,
            )
        except ControlledProbeError as exc:
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=False,
                connect_ms=None,
                exit_ip=None,
                multihost_failure_reason="connect_failed_before_multihost",
                multihost_summary=self._skipped_multihost_summary("connect_failed_before_multihost"),
                error_code=exc.code.value,
                error_text=self._short_error_text(exc),
            )
        except requests.RequestException as exc:
            error_code = classify_request_exception(exc)
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=False,
                connect_ms=None,
                exit_ip=None,
                multihost_failure_reason="connect_failed_before_multihost",
                multihost_summary=self._skipped_multihost_summary("connect_failed_before_multihost"),
                error_code=error_code.value,
                error_text=self._short_error_text(exc),
            )
        except Exception as exc:  # defensive safety net per-candidate
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=False,
                connect_ms=None,
                exit_ip=None,
                multihost_failure_reason="connect_failed_before_multihost",
                multihost_summary=self._skipped_multihost_summary("connect_failed_before_multihost"),
                error_code=ProbeErrorCode.UNEXPECTED_ERROR.value,
                error_text=self._short_error_text(exc),
            )

    def _resolve_exit_ip(self, proxies: dict[str, str]) -> str:
        last_request_error: requests.RequestException | None = None
        invalid_payload_errors: list[str] = []

        for endpoint in self._exit_ip_urls:
            try:
                response = self._session.get(
                    endpoint,
                    proxies=proxies,
                    timeout=self._timeout,
                    headers={"User-Agent": _EXIT_IP_USER_AGENT},
                )
                response.raise_for_status()
                return self._extract_ip_from_response(response)
            except requests.RequestException as exc:
                last_request_error = exc
            except ControlledProbeError as exc:
                invalid_payload_errors.append(exc.text)

        if last_request_error is not None:
            raise last_request_error

        message = "; ".join(invalid_payload_errors) if invalid_payload_errors else "No exit IP endpoint produced a valid response"
        raise ControlledProbeError(code=ProbeErrorCode.PROBE_FAILED, text=message)

    def _build_runtime_proxies(self, listen_port: int) -> dict[str, str]:
        runtime = self._runtime
        if runtime is None:
            raise ControlledProbeError(
                code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
                text="sing-box runtime is not initialized",
            )

        proxy_url = f"http://{runtime.bind_host}:{listen_port}"
        return {"http": proxy_url, "https": proxy_url}

    def _try_speed_test(
        self,
        *,
        candidate_id: str,
        listen_port: int,
        proxies: dict[str, str],
    ) -> SpeedMeasurement | None:
        if not self._speed_test_urls:
            logger.warning(
                "Speed test skipped: no speed endpoints configured",
                extra={"candidate_id": candidate_id},
            )
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

        logger.info(
            "Speed test started",
            extra={
                "candidate_id": candidate_id,
                "listen_port": listen_port,
                "speed_test_urls": self._speed_test_urls,
                "speed_test_attempts": self._speed_test_attempts,
                "speed_test_timeout": self._speed_test_timeout,
                "speed_test_max_bytes": self._speed_test_max_bytes,
                "speed_test_chunk_size": self._speed_test_chunk_size,
            },
        )

        try:
            result = run_speed_measurement(
                session=self._session,
                urls=self._speed_test_urls,
                proxies=proxies,
                timeout=self._speed_test_timeout,
                max_bytes=self._speed_test_max_bytes,
                chunk_size=self._speed_test_chunk_size,
                user_agent=_SPEED_TEST_USER_AGENT,
                attempts=self._speed_test_attempts,
            )
        except Exception as exc:
            result = self._unexpected_speed_failure(exc)
            logger.warning(
                "Speed test failed",
                extra={
                    "candidate_id": candidate_id,
                    "listen_port": listen_port,
                    "speed_error_code": result.error_code.value if result.error_code else None,
                    "speed_failure_reason": result.failure_reason.value if result.failure_reason else None,
                    "speed_error_text": result.error_text,
                    "speed_endpoint_url": result.endpoint_url,
                    "speed_attempts": result.attempts,
                    "speed_successes": result.successes,
                },
            )
            return result

        if result.success:
            logger.info(
                "Speed test completed",
                extra={
                    "candidate_id": candidate_id,
                    "listen_port": listen_port,
                    "bytes_read": result.bytes_read,
                    "first_byte_ms": result.first_byte_ms,
                    "download_mbps": str(result.download_mbps),
                    "speed_endpoint_url": result.endpoint_url,
                    "speed_attempts": result.attempts,
                    "speed_successes": result.successes,
                },
            )
        else:
            logger.warning(
                "Speed test unavailable",
                extra={
                    "candidate_id": candidate_id,
                    "listen_port": listen_port,
                    "first_byte_ms": result.first_byte_ms,
                    "bytes_read": result.bytes_read,
                    "speed_error_code": result.error_code.value if result.error_code else None,
                    "speed_failure_reason": result.failure_reason.value if result.failure_reason else None,
                    "speed_error_text": result.error_text,
                    "speed_endpoint_url": result.endpoint_url,
                    "speed_attempts": result.attempts,
                    "speed_successes": result.successes,
                },
            )
        return result

    def _run_multihost_measurement(self, *, proxies: dict[str, str]) -> MultiHostMeasurement:
        return run_multihost_measurement(
            session=self._session,
            proxies=proxies,
            timeout=self._timeout,
            baseline_urls=self._baseline_urls,
            critical_urls=self._critical_urls,
            max_target_first_byte_ms=self._max_target_first_byte_ms,
            max_target_latency_ms=self._max_target_latency_ms,
            min_user_target_success_ratio=self._min_user_target_success_ratio,
            require_critical_targets_all_success=self._require_critical_targets_all_success,
            min_critical_target_success_ratio=self._min_critical_target_success_ratio,
            enabled=self._multihost_enabled,
        )

    def _build_multihost_summary(
        self,
        *,
        multihost_result: MultiHostMeasurement,
        speed_result: SpeedMeasurement | None,
    ) -> dict[str, Any]:
        summary = multihost_result.to_summary_json(
            min_user_target_success_ratio=self._min_user_target_success_ratio,
            require_critical_targets_all_success=self._require_critical_targets_all_success,
            min_critical_target_success_ratio=self._min_critical_target_success_ratio,
            max_target_first_byte_ms=self._max_target_first_byte_ms,
            max_target_latency_ms=self._max_target_latency_ms,
        )

        summary["groups"]["speed"] = {
            "configured_endpoints": list(self._speed_test_urls),
            "attempts": speed_result.attempts if speed_result else 0,
            "successes": speed_result.successes if speed_result else 0,
            "measured": bool(speed_result and speed_result.download_mbps is not None),
            "endpoint_url": speed_result.endpoint_url if speed_result else None,
            "failure_reason": (
                speed_result.failure_reason.value
                if speed_result and speed_result.failure_reason
                else None
            ),
        }
        return summary

    def _unexpected_speed_failure(self, exc: Exception) -> SpeedMeasurement:
        endpoint_url = self._speed_test_urls[0] if self._speed_test_urls else None
        return SpeedMeasurement(
            first_byte_ms=None,
            download_mbps=None,
            bytes_read=0,
            endpoint_url=endpoint_url,
            attempts=1,
            successes=0,
            error_code=SpeedFailureCode.UNEXPECTED_ERROR,
            failure_reason=SpeedFailureCode.UNEXPECTED_ERROR,
            error_text=f"Unexpected speed measurement error: {self._short_error_text(exc)}",
        )

    def _extract_ip_from_response(self, response: requests.Response) -> str:
        candidate_ip: str | None = None

        try:
            payload = response.json()
            if isinstance(payload, dict):
                value = payload.get("ip")
                if isinstance(value, str):
                    candidate_ip = value.strip()
        except ValueError:
            pass

        if not candidate_ip:
            text = response.text.strip()
            candidate_ip = text.splitlines()[0] if text else None

        if not candidate_ip:
            raise ControlledProbeError(
                code=ProbeErrorCode.PROBE_FAILED,
                text="Exit IP endpoint returned empty payload",
            )

        try:
            return str(ipaddress.ip_address(candidate_ip))
        except ValueError as exc:
            raise ControlledProbeError(
                code=ProbeErrorCode.PROBE_FAILED,
                text=f"Exit IP endpoint returned invalid IP: {candidate_ip[:120]}",
            ) from exc

    @staticmethod
    def _build_exit_ip_urls(primary_url: str) -> tuple[str, ...]:
        values: list[str] = []
        trimmed_primary = primary_url.strip()
        if trimmed_primary:
            values.append(trimmed_primary)

        for fallback in _FALLBACK_EXIT_IP_URLS:
            if fallback not in values:
                values.append(fallback)

        return tuple(values)

    @staticmethod
    def _short_error_text(exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            message = exc.__class__.__name__
        return message[:500]

    def _skipped_multihost_summary(self, reason: str) -> dict[str, Any]:
        return {
            "enabled": self._multihost_enabled,
            "passed_policy": False,
            "failure_reason": reason,
            "policy": {
                "min_user_target_success_ratio": self._min_user_target_success_ratio,
                "require_critical_targets_all_success": self._require_critical_targets_all_success,
                "min_critical_target_success_ratio": self._min_critical_target_success_ratio,
                "max_target_first_byte_ms": self._max_target_first_byte_ms,
                "max_target_latency_ms": self._max_target_latency_ms,
            },
            "groups": {
                "baseline": {"total": 0, "successful": 0},
                "critical": {"total": 0, "successful": 0, "success_ratio": None},
                "speed": {
                    "configured_endpoints": list(self._speed_test_urls),
                    "attempts": 0,
                    "successes": 0,
                    "measured": False,
                    "endpoint_url": None,
                    "failure_reason": None,
                },
            },
            "user_targets": {"total": 0, "successful": 0, "success_ratio": None},
            "critical_targets": {"total": 0, "successful": 0, "all_success": True},
            "targets": [],
        }
