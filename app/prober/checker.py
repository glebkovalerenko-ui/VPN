"""Probe backend implementation for Stage 5 liveness + exit-IP checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from time import perf_counter

import requests

from .config_builder import build_outbound_config, build_probe_config
from .errors import ControlledProbeError, ProbeErrorCode, classify_request_exception
from .selectors import ProbeCandidate
from .singbox import SingBoxRuntime

_FALLBACK_EXIT_IP_URLS: tuple[str, ...] = (
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
)


@dataclass(slots=True, frozen=True)
class ProbeResult:
    """Normalized output of one candidate probe attempt."""

    checked_at: datetime
    connect_ok: bool
    connect_ms: int | None
    exit_ip: str | None
    error_code: str | None
    error_text: str | None


class ProbeBackend:
    """Base contract for probe backends."""

    def probe_candidate(self, candidate: ProbeCandidate) -> ProbeResult:
        raise NotImplementedError


class SingBoxProbeBackend(ProbeBackend):
    """Stage 5 runtime backend that probes candidates through sing-box subprocess."""

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
                exit_ip = self._resolve_exit_ip(listen_port)

            connect_ms = max(1, int((perf_counter() - started_at) * 1000))
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=True,
                connect_ms=connect_ms,
                exit_ip=exit_ip,
                error_code=None,
                error_text=None,
            )
        except ControlledProbeError as exc:
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=False,
                connect_ms=None,
                exit_ip=None,
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
                error_code=error_code.value,
                error_text=self._short_error_text(exc),
            )
        except Exception as exc:  # defensive safety net per-candidate
            return ProbeResult(
                checked_at=checked_at,
                connect_ok=False,
                connect_ms=None,
                exit_ip=None,
                error_code=ProbeErrorCode.UNEXPECTED_ERROR.value,
                error_text=self._short_error_text(exc),
            )

    def _resolve_exit_ip(self, listen_port: int) -> str:
        runtime = self._runtime
        if runtime is None:
            raise ControlledProbeError(
                code=ProbeErrorCode.BACKEND_NOT_AVAILABLE,
                text="sing-box runtime is not initialized",
            )

        proxy_url = f"http://{runtime.bind_host}:{listen_port}"
        proxies = {"http": proxy_url, "https": proxy_url}

        last_request_error: requests.RequestException | None = None
        invalid_payload_errors: list[str] = []

        for endpoint in self._exit_ip_urls:
            try:
                response = self._session.get(
                    endpoint,
                    proxies=proxies,
                    timeout=self._timeout,
                    headers={"User-Agent": "proxy-mvp-stage5-prober/2.0"},
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
