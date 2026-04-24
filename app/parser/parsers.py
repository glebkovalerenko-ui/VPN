"""Line parsers for supported proxy config schemes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Final
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit

from .utils import decode_base64_flexible, extract_country_tag, normalize_host, parse_port

SUPPORTED_SCHEMES: Final[set[str]] = {
    "vless",
    "vmess",
    "trojan",
    "ss",
    "hysteria2",
    "hy2",
    "tuic",
    "socks",
    "http",
}

_SCHEME_ALIAS: Final[dict[str, str]] = {
    "hy2": "hysteria2",
}

_SNI_PARAM_KEYS: Final[tuple[str, ...]] = (
    "sni",
    "servername",
    "server-name",
    "peer",
    "host",
)


@dataclass(slots=True, frozen=True)
class ParsedProxyLine:
    """Normalized candidate fields extracted from one config line."""

    raw_config: str
    protocol: str
    host: str | None
    port: int | None
    sni: str | None
    canonical_auth: str | None
    source_country_tag: str | None
    partially_parsed: bool


def parse_proxy_line(line: str) -> ParsedProxyLine | None:
    """Parse one snapshot line into normalized candidate fields."""
    split = urlsplit(line)
    scheme = split.scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        return None

    protocol = _SCHEME_ALIAS.get(scheme, scheme)
    if protocol == "vmess":
        return _parse_vmess(line, split)
    if protocol == "ss":
        return _parse_ss(line, split)

    parsed = _parse_url_like(line, split, protocol)
    if protocol == "http" and parsed.port is None:
        return None
    return parsed


def _parse_url_like(raw_line: str, split: SplitResult, protocol: str) -> ParsedProxyLine:
    host = normalize_host(split.hostname)
    port = _safe_split_port(split)
    query_values = parse_qs(split.query, keep_blank_values=False)

    sni = _extract_sni(query_values)
    fragment_label = unquote(split.fragment) if split.fragment else None
    country_tag = extract_country_tag(fragment_label)
    auth = _build_auth_from_split(split)

    return ParsedProxyLine(
        raw_config=raw_line,
        protocol=protocol,
        host=host,
        port=port,
        sni=sni,
        canonical_auth=auth,
        source_country_tag=country_tag,
        partially_parsed=host is None or port is None,
    )


def _parse_vmess(raw_line: str, split: SplitResult) -> ParsedProxyLine:
    payload_token = _extract_vmess_payload_token(raw_line)
    payload = _decode_vmess_payload_json(payload_token)

    # Stage 4 requirement: for vmess always try base64-json payload first.
    # URL-like vmess format remains as fallback for non-payload lines.
    if payload is None:
        return _parse_url_like(raw_line, split, "vmess")

    fragment_label = unquote(split.fragment) if split.fragment else None
    host = normalize_host(
        _first_non_empty(
            payload.get("add"),
            payload.get("server"),
            payload.get("host"),
        )
    )
    port = parse_port(payload.get("port"))
    sni = _first_non_empty(
        payload.get("sni"),
        payload.get("serverName"),
        payload.get("servername"),
    )
    auth = _build_vmess_auth(payload)
    country_tag = extract_country_tag(
        fragment_label,
        payload.get("ps"),
        payload.get("remark"),
        payload.get("name"),
    )

    return ParsedProxyLine(
        raw_config=raw_line,
        protocol="vmess",
        host=host,
        port=port,
        sni=_normalize_optional_text(sni),
        canonical_auth=auth,
        source_country_tag=country_tag,
        partially_parsed=host is None or port is None,
    )


def _extract_vmess_payload_token(raw_line: str) -> str | None:
    _, sep, tail = raw_line.partition("://")
    if not sep:
        return None

    candidate = tail.strip()
    if not candidate:
        return None

    for delimiter in ("#", "?"):
        if delimiter in candidate:
            candidate = candidate.split(delimiter, 1)[0]

    token = candidate.strip().lstrip("/")
    return token or None


def _decode_vmess_payload_json(payload_token: str | None) -> dict[str, object] | None:
    if not payload_token:
        return None

    decoded = decode_base64_flexible(payload_token)
    if not decoded:
        return None

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    if not _looks_like_vmess_payload(payload):
        return None
    return payload


def _looks_like_vmess_payload(payload: dict[str, object]) -> bool:
    keys = {str(key).lower() for key in payload}
    has_endpoint = any(key in keys for key in ("add", "server", "host"))
    has_port = "port" in keys
    has_identity = any(key in keys for key in ("id", "uuid"))
    return has_endpoint and (has_port or has_identity)


def _parse_ss(raw_line: str, split: SplitResult) -> ParsedProxyLine:
    query_values = parse_qs(split.query, keep_blank_values=False)
    fragment_label = unquote(split.fragment) if split.fragment else None

    host = normalize_host(split.hostname)
    port = _safe_split_port(split)
    auth = _build_auth_from_split(split)

    if auth and ":" not in auth:
        decoded_auth = decode_base64_flexible(auth)
        if decoded_auth:
            auth = decoded_auth

    if host is None or port is None:
        fallback_token = (split.netloc or split.path).lstrip("/")
        decoded = decode_base64_flexible(fallback_token)
        if decoded and "@" in decoded:
            auth_part, host_part = decoded.rsplit("@", 1)
            host_fallback, port_fallback = _split_host_port(host_part)
            host = normalize_host(host_fallback)
            port = parse_port(port_fallback)
            auth = auth or auth_part

    sni = _extract_sni(query_values)
    if sni is None:
        sni = _extract_sni_from_plugin(query_values.get("plugin"))

    return ParsedProxyLine(
        raw_config=raw_line,
        protocol="ss",
        host=host,
        port=port,
        sni=sni,
        canonical_auth=_normalize_optional_text(auth),
        source_country_tag=extract_country_tag(fragment_label),
        partially_parsed=host is None or port is None,
    )


def _safe_split_port(split: SplitResult) -> int | None:
    try:
        return split.port
    except ValueError:
        return None


def _extract_sni(query_values: dict[str, list[str]]) -> str | None:
    normalized = {key.lower(): values for key, values in query_values.items()}
    for key in _SNI_PARAM_KEYS:
        values = normalized.get(key)
        if not values:
            continue
        value = _normalize_optional_text(values[0])
        if value:
            return value
    return None


def _extract_sni_from_plugin(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        parts = value.split(";")
        for part in parts:
            lowered = part.lower()
            if lowered.startswith("obfs-host="):
                return _normalize_optional_text(part.split("=", 1)[1])
            if lowered.startswith("host="):
                return _normalize_optional_text(part.split("=", 1)[1])
    return None


def _build_auth_from_split(split: SplitResult) -> str | None:
    username = unquote(split.username) if split.username else None
    password = unquote(split.password) if split.password else None

    if username and password:
        return f"{username}:{password}"
    if username:
        return username
    return None


def _build_vmess_auth(payload: dict[str, object]) -> str | None:
    uid = _normalize_optional_text(_obj_to_text(payload.get("id")))
    if not uid:
        return None
    aid = _normalize_optional_text(_obj_to_text(payload.get("aid")))
    return f"{uid}:{aid}" if aid else uid


def _obj_to_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _split_host_port(raw_value: str) -> tuple[str | None, str | None]:
    value = raw_value.strip()
    if not value:
        return None, None

    if value.startswith("[") and "]" in value:
        host_end = value.find("]")
        host = value[1:host_end]
        rest = value[host_end + 1 :]
        if rest.startswith(":"):
            return host, rest[1:]
        return host, None

    if ":" not in value:
        return value, None

    host, port = value.rsplit(":", 1)
    return host, port


def _first_non_empty(*values: object) -> str | None:
    for value in values:
        text_value = _normalize_optional_text(_obj_to_text(value))
        if text_value:
            return text_value
    return None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
