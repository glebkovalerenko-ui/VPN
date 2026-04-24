"""Geo provider implementations for Stage 6."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

import requests

from .errors import GeoErrorCode, GeoProviderError
from .models import GeoLookupResult
from .utils import (
    looks_like_throttling_message,
    normalize_country_code,
    normalize_ip,
)


class GeoProvider(ABC):
    """Base contract for geo providers."""

    name: str

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: int,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        if not self._base_url:
            raise ValueError(f"{self.__class__.__name__}: base_url must not be empty")
        self._request_timeout_seconds = request_timeout_seconds
        self._session = session or requests.Session()

    def lookup_country(self, ip: str) -> GeoLookupResult:
        """Resolve country by IP using provider-specific API."""
        normalized_ip = normalize_ip(ip)
        if normalized_ip is None:
            return GeoLookupResult.failed_result(
                ip=ip,
                provider_name=self.name,
                error_code=GeoErrorCode.INVALID_RESPONSE,
                error_text="Invalid IP value for geo lookup",
            )

        try:
            country_code = self._lookup_country_code(normalized_ip)
            return GeoLookupResult.success_result(
                ip=normalized_ip,
                country_code=country_code,
                provider_name=self.name,
            )
        except GeoProviderError as exc:
            return GeoLookupResult.failed_result(
                ip=normalized_ip,
                provider_name=self.name,
                error_code=exc.code,
                error_text=self._short_error_text(exc.text),
            )
        except Exception as exc:
            return GeoLookupResult.failed_result(
                ip=normalized_ip,
                provider_name=self.name,
                error_code=GeoErrorCode.PROVIDER_FAILED,
                error_text=self._short_error_text(str(exc) or exc.__class__.__name__),
            )

    @abstractmethod
    def _lookup_country_code(self, ip: str) -> str:
        """Provider-specific country resolution logic."""

    def _request_json(self, *, ip: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = self._build_lookup_url(ip)

        try:
            response = self._session.get(
                url,
                params=params,
                timeout=self._request_timeout_seconds,
                headers={"User-Agent": f"proxy-mvp-stage6-geo/{self.name}"},
            )
        except requests.Timeout as exc:
            raise GeoProviderError(
                code=GeoErrorCode.TIMEOUT,
                text=f"Geo provider timeout after {self._request_timeout_seconds}s",
            ) from exc
        except requests.RequestException as exc:
            raise GeoProviderError(
                code=GeoErrorCode.PROVIDER_FAILED,
                text=f"Geo provider request failed: {self._short_error_text(str(exc))}",
            ) from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            details = "Geo provider throttled request (HTTP 429)"
            if retry_after:
                details = f"{details}, retry_after={retry_after}"
            raise GeoProviderError(
                code=GeoErrorCode.THROTTLED,
                text=details,
                status_code=429,
            )

        if response.status_code >= 400:
            raise GeoProviderError(
                code=GeoErrorCode.PROVIDER_HTTP_ERROR,
                text=f"Geo provider returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GeoProviderError(
                code=GeoErrorCode.INVALID_RESPONSE,
                text="Geo provider returned non-JSON payload",
            ) from exc

        if not isinstance(payload, dict):
            raise GeoProviderError(
                code=GeoErrorCode.INVALID_RESPONSE,
                text="Geo provider returned non-object JSON payload",
            )

        return payload

    def _extract_country_code(
        self,
        payload: Mapping[str, Any],
        *,
        field_names: tuple[str, ...],
    ) -> str:
        for field_name in field_names:
            value = payload.get(field_name)
            if not isinstance(value, str):
                continue
            normalized = normalize_country_code(value)
            if normalized:
                return normalized

        raise GeoProviderError(
            code=GeoErrorCode.INVALID_RESPONSE,
            text=f"Geo provider payload missing valid country code fields: {', '.join(field_names)}",
        )

    def _build_lookup_url(self, ip: str) -> str:
        return f"{self._base_url}/{ip}"

    @staticmethod
    def _short_error_text(message: str, *, max_chars: int = 500) -> str:
        trimmed = message.strip()
        if not trimmed:
            return "unknown error"
        return trimmed[:max_chars]


class IpApiGeoProvider(GeoProvider):
    """Geo lookup via ip-api.com/json endpoint."""

    name = "ip-api"

    def _lookup_country_code(self, ip: str) -> str:
        payload = self._request_json(
            ip=ip,
            params={"fields": "status,message,countryCode,query"},
        )
        status = str(payload.get("status") or "").strip().lower()
        if status != "success":
            message = str(payload.get("message") or "ip-api lookup failed").strip()
            error_code = (
                GeoErrorCode.THROTTLED
                if looks_like_throttling_message(message)
                else GeoErrorCode.PROVIDER_FAILED
            )
            raise GeoProviderError(code=error_code, text=message)

        return self._extract_country_code(payload, field_names=("countryCode",))


class IpWhoisGeoProvider(GeoProvider):
    """Geo lookup via ipwhois-compatible JSON endpoint."""

    name = "ipwhois"

    def _lookup_country_code(self, ip: str) -> str:
        payload = self._request_json(ip=ip)

        success_value = payload.get("success")
        if success_value is False:
            message = str(payload.get("message") or payload.get("reason") or "ipwhois lookup failed").strip()
            error_code = (
                GeoErrorCode.THROTTLED
                if looks_like_throttling_message(message)
                else GeoErrorCode.PROVIDER_FAILED
            )
            raise GeoProviderError(code=error_code, text=message)

        return self._extract_country_code(
            payload,
            field_names=("country_code", "countryCode", "country_code2"),
        )
