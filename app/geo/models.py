"""Typed result models for geo lookups."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import GeoErrorCode


@dataclass(slots=True, frozen=True)
class GeoLookupResult:
    """Normalized output for one geo provider lookup attempt."""

    ip: str
    country_code: str | None
    provider_name: str
    success: bool
    error_code: str | None = None
    error_text: str | None = None

    @classmethod
    def success_result(cls, *, ip: str, country_code: str, provider_name: str) -> "GeoLookupResult":
        return cls(
            ip=ip,
            country_code=country_code,
            provider_name=provider_name,
            success=True,
            error_code=None,
            error_text=None,
        )

    @classmethod
    def failed_result(
        cls,
        *,
        ip: str,
        provider_name: str,
        error_code: GeoErrorCode | str,
        error_text: str,
    ) -> "GeoLookupResult":
        return cls(
            ip=ip,
            country_code=None,
            provider_name=provider_name,
            success=False,
            error_code=str(error_code),
            error_text=error_text,
        )
