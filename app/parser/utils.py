"""Utility helpers for Stage 4 parser."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final
from urllib.parse import unquote


_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_HEADER_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z][A-Za-z0-9_-]{1,64})\s*:")
_TEXT_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]{2,20}")
_BASE64_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9+/=_-]+$")

_KNOWN_HEADER_KEYS: Final[set[str]] = {
    "profile-title",
    "profile-update-interval",
    "profile-web-page-url",
    "profile-url",
    "subscription-userinfo",
    "subscription-info",
    "last-modified",
    "content-disposition",
    "user-agent",
    "content-type",
    "content-length",
    "upload",
    "download",
    "total",
    "expire",
}

_COUNTRY_ALIASES: Final[dict[str, str]] = {
    "ru": "RU",
    "rus": "RU",
    "russia": "RU",
    "moscow": "RU",
    "spb": "RU",
    "ua": "UA",
    "ukraine": "UA",
    "us": "US",
    "usa": "US",
    "america": "US",
    "unitedstates": "US",
    "uk": "GB",
    "gb": "GB",
    "gbr": "GB",
    "britain": "GB",
    "england": "GB",
    "de": "DE",
    "germany": "DE",
    "fr": "FR",
    "france": "FR",
    "nl": "NL",
    "netherlands": "NL",
    "jp": "JP",
    "japan": "JP",
    "sg": "SG",
    "singapore": "SG",
    "hk": "HK",
    "hongkong": "HK",
    "tr": "TR",
    "turkey": "TR",
    "fi": "FI",
    "finland": "FI",
    "se": "SE",
    "sweden": "SE",
    "no": "NO",
    "norway": "NO",
    "pl": "PL",
    "poland": "PL",
    "kz": "KZ",
    "kazakhstan": "KZ",
    "ae": "AE",
    "uae": "AE",
    "ca": "CA",
    "canada": "CA",
    "br": "BR",
    "brazil": "BR",
    "au": "AU",
    "australia": "AU",
    "in": "IN",
    "india": "IN",
    "id": "ID",
    "indonesia": "ID",
    "kr": "KR",
    "korea": "KR",
    "tw": "TW",
    "taiwan": "TW",
    "cn": "CN",
    "china": "CN",
}


def normalize_input_line(raw_line: str) -> str:
    """Return stripped line from snapshot payload."""
    return raw_line.strip()


def looks_like_header_line(line: str) -> bool:
    """Heuristic filter for known service/header lines in TXT subscriptions."""
    if not line:
        return True
    if line.startswith("#"):
        return True
    if _SCHEME_RE.match(line):
        return False

    lower_line = line.lower()
    match = _HEADER_KEY_RE.match(lower_line)
    if not match:
        return False

    key = match.group(1)
    if key in _KNOWN_HEADER_KEYS:
        return True

    return key.startswith(("profile-", "subscription-", "content-"))


def decode_base64_flexible(payload: str) -> str | None:
    """Try decoding URL-safe/base64 payload to UTF-8 text."""
    token = payload.strip()
    if not token:
        return None
    if not _BASE64_TOKEN_RE.fullmatch(token):
        return None

    normalized = token.replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)

    try:
        decoded = base64.b64decode(normalized, validate=True)
        if decoded:
            return decoded.decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        pass

    return None


def normalize_host(host: str | None) -> str | None:
    """Normalize host value for storage/fingerprint."""
    if host is None:
        return None
    normalized = host.strip().strip("[]").rstrip(".").lower()
    return normalized or None


def parse_port(value: str | int | None) -> int | None:
    """Convert value to TCP/UDP port if valid."""
    if value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def extract_country_tag(*candidates: str | None) -> str | None:
    """Extract ISO-like country tag from label/fragment candidates."""
    for candidate in candidates:
        tag = _extract_country_tag_from_text(candidate)
        if tag:
            return tag
    return None


def _extract_country_tag_from_text(text: str | None) -> str | None:
    if not text:
        return None

    decoded = unquote(text).strip()
    if not decoded:
        return None

    flag_code = _extract_flag_emoji_country(decoded)
    if flag_code:
        return flag_code

    normalized = decoded.replace("_", " ").replace("-", " ").lower()
    for token in _TEXT_TOKEN_RE.findall(normalized):
        mapped = _COUNTRY_ALIASES.get(token)
        if mapped:
            return mapped
    return None


def _extract_flag_emoji_country(text: str) -> str | None:
    indicators = [ch for ch in text if 0x1F1E6 <= ord(ch) <= 0x1F1FF]
    if len(indicators) < 2:
        return None

    first, second = indicators[0], indicators[1]
    return "".join(
        chr(ord(ch) - 0x1F1E6 + ord("A"))
        for ch in (first, second)
    )
