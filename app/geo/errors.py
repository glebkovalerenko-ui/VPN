"""Error taxonomy for Stage 6 geo lookups."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GeoErrorCode(StrEnum):
    """Stable error codes for geo provider failures."""

    TIMEOUT = "timeout"
    PROVIDER_HTTP_ERROR = "provider_http_error"
    THROTTLED = "throttled"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_FAILED = "provider_failed"


@dataclass(slots=True, frozen=True)
class GeoProviderError(Exception):
    """Controlled provider error for predictable fallback handling."""

    code: GeoErrorCode
    text: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.text
