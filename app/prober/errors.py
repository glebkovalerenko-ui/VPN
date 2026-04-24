"""Error taxonomy and helpers for Stage 5 prober."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import requests


class ProbeErrorCode(StrEnum):
    """Stable error codes persisted into proxy_checks.error_code."""

    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    BACKEND_NOT_AVAILABLE = "backend_not_available"
    BACKEND_START_TIMEOUT = "backend_start_timeout"
    CONNECT_TIMEOUT = "connect_timeout"
    PROBE_FAILED = "probe_failed"
    UNEXPECTED_ERROR = "unexpected_error"


@dataclass(slots=True, frozen=True)
class ControlledProbeError(Exception):
    """Expected probe failure that should not be treated as crash."""

    code: ProbeErrorCode
    text: str

    def __str__(self) -> str:
        return self.text


def classify_request_exception(exc: requests.RequestException) -> ProbeErrorCode:
    """Map requests-layer exceptions to persisted probe error code."""
    if isinstance(exc, requests.Timeout):
        return ProbeErrorCode.CONNECT_TIMEOUT
    return ProbeErrorCode.PROBE_FAILED
