"""Geo service orchestration with primary/fallback providers."""

from __future__ import annotations

from app.common.logging import get_logger
from app.common.settings import Settings

from .errors import GeoErrorCode
from .models import GeoLookupResult
from .providers import GeoProvider, IpApiGeoProvider, IpWhoisGeoProvider
from .utils import normalize_country_reference, normalize_provider_name

logger = get_logger(__name__)


class GeoService:
    """Resolve countries with configured primary/fallback providers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: dict[str, GeoProvider] = {
            "ip-api": IpApiGeoProvider(
                base_url=settings.GEO_IP_API_BASE_URL,
                request_timeout_seconds=settings.GEO_REQUEST_TIMEOUT_SECONDS,
            ),
            "ipwhois": IpWhoisGeoProvider(
                base_url=settings.GEO_IPWHOIS_BASE_URL,
                request_timeout_seconds=settings.GEO_REQUEST_TIMEOUT_SECONDS,
            ),
        }
        self._provider_order = self._resolve_provider_order()

    def resolve_country(self, ip: str) -> GeoLookupResult:
        """Resolve country by IP using primary/fallback provider sequence."""
        if not self._provider_order:
            return GeoLookupResult.failed_result(
                ip=ip,
                provider_name="geo-service",
                error_code=GeoErrorCode.PROVIDER_FAILED,
                error_text="No valid geo providers configured",
            )

        last_failure: GeoLookupResult | None = None
        for index, provider_name in enumerate(self._provider_order):
            provider = self._providers[provider_name]
            result = provider.lookup_country(ip)
            if result.success:
                if index > 0:
                    logger.info(
                        "Geo lookup resolved via fallback provider",
                        extra={
                            "ip": result.ip,
                            "provider_name": result.provider_name,
                            "primary_provider": self._provider_order[0],
                        },
                    )
                return result

            last_failure = result

        if last_failure is not None:
            return last_failure

        return GeoLookupResult.failed_result(
            ip=ip,
            provider_name="geo-service",
            error_code=GeoErrorCode.PROVIDER_FAILED,
            error_text="Geo lookup failed without provider result",
        )

    def _resolve_provider_order(self) -> tuple[str, ...]:
        configured_names = [
            normalize_provider_name(self._settings.GEO_PROVIDER_PRIMARY),
            normalize_provider_name(self._settings.GEO_PROVIDER_FALLBACK),
        ]

        ordered: list[str] = []
        for provider_name in configured_names:
            if provider_name is None or provider_name in ordered:
                continue
            if provider_name not in self._providers:
                logger.warning(
                    "Unknown geo provider configured",
                    extra={"provider_name": provider_name},
                )
                continue
            ordered.append(provider_name)

        return tuple(ordered)


def resolve_country(ip: str, settings: Settings) -> GeoLookupResult:
    """Helper for one-off lookups without explicit GeoService lifecycle."""
    return GeoService(settings).resolve_country(ip)


def compute_geo_match(source_country_tag: str | None, exit_country: str | None) -> bool | None:
    """Compare source tag and resolved exit country in normalized uppercase form."""
    source_country = normalize_country_reference(source_country_tag)
    if source_country is None:
        return None

    resolved_exit_country = normalize_country_reference(exit_country)
    if resolved_exit_country is None:
        return None

    return source_country == resolved_exit_country
