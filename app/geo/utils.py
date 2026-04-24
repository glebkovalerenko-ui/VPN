"""Utility helpers for Stage 6 geo provider layer."""

from __future__ import annotations

import ipaddress
import re
from typing import Final

_ISO_ALPHA2_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}$")
_THROTTLE_HINTS: Final[tuple[str, ...]] = (
    "limit",
    "throttle",
    "too many",
    "quota",
    "429",
)
_PROVIDER_NAME_ALIASES: Final[dict[str, str]] = {
    "ip-api": "ip-api",
    "ip_api": "ip-api",
    "ipapi": "ip-api",
    "ipwhois": "ipwhois",
    "ipwhois.io": "ipwhois",
    "ipwho.is": "ipwhois",
}


def normalize_ip(value: str) -> str | None:
    """Normalize IPv4/IPv6 string to canonical representation."""
    candidate = value.strip()
    if not candidate:
        return None

    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def normalize_country_code(value: str | None) -> str | None:
    """Normalize provider country code to uppercase ISO alpha-2."""
    if value is None:
        return None

    normalized = value.strip().upper()
    if not normalized:
        return None

    if not _ISO_ALPHA2_RE.fullmatch(normalized):
        return None
    return normalized


def normalize_country_reference(value: str | None) -> str | None:
    """Normalize reference country tag for case-insensitive matching."""
    if value is None:
        return None

    normalized = value.strip().upper()
    return normalized or None


def normalize_provider_name(value: str | None) -> str | None:
    """Normalize provider name from settings to internal key."""
    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None
    return _PROVIDER_NAME_ALIASES.get(normalized, normalized)


def looks_like_throttling_message(message: str | None) -> bool:
    """Heuristic check for provider throttling payloads."""
    if not message:
        return False

    normalized = message.lower()
    return any(token in normalized for token in _THROTTLE_HINTS)
