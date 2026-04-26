"""Display-label relabeling helpers for Stage 9 exporter output."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .models import ExportCandidate

_UNKNOWN_FLAG = "\U0001F3F3\ufe0f"
_UNKNOWN_COUNTRY = "ZZ"
_UNKNOWN_SPEED = "NS"
_UNKNOWN_LATENCY = "NAms"
_UNKNOWN_RANK = "#-"
_UNKNOWN_GROUP = "UNK"
_STANDARD_LABEL_STRATEGY = "standardized_v1_current_country"

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_GROUP_BY_FAMILY = {
    "black": "BLK",
    "white_cidr": "CIDR",
    "white_sni": "SNI",
}
_VMESS_REQUIRED_FIELDS = ("add", "port", "id")


@dataclass(slots=True, frozen=True)
class RelabeledRawLink:
    """Relabeling result used by TXT writer and debug JSON payload."""

    display_label: str
    source_raw_config: str | None
    export_raw_config: str | None
    label_country: str
    label_flag: str
    label_group: str
    label_download_mbps: str
    label_latency_ms: str
    label_rank_global: str
    label_rank_in_family: str
    label_strategy: str
    label_error_code: str | None = None
    label_error_text: str | None = None


@dataclass(slots=True, frozen=True)
class _LabelParts:
    display_label: str
    country: str
    flag: str
    group: str
    speed: str
    latency: str
    rank_global: str
    rank_in_family: str


@dataclass(slots=True, frozen=True)
class _RelabelOutcome:
    export_raw_config: str
    relabel_strategy: str
    error_code: str | None = None
    error_text: str | None = None


def build_relabeled_raw_link(candidate: ExportCandidate) -> RelabeledRawLink:
    """Build standardized display label and relabel raw link without touching connection fields."""
    label_parts = _build_label_parts(candidate)
    source_raw_config = (candidate.raw_config or "").strip() or None
    outcome = _relabel_raw_config(source_raw_config, label_parts.display_label)

    return RelabeledRawLink(
        display_label=label_parts.display_label,
        source_raw_config=source_raw_config,
        export_raw_config=outcome.export_raw_config,
        label_country=label_parts.country,
        label_flag=label_parts.flag,
        label_group=label_parts.group,
        label_download_mbps=label_parts.speed,
        label_latency_ms=label_parts.latency,
        label_rank_global=label_parts.rank_global,
        label_rank_in_family=label_parts.rank_in_family,
        label_strategy=f"{_STANDARD_LABEL_STRATEGY}:{outcome.relabel_strategy}",
        label_error_code=outcome.error_code,
        label_error_text=outcome.error_text,
    )


def _build_label_parts(candidate: ExportCandidate) -> _LabelParts:
    country, flag = _country_and_flag(candidate.current_country)
    group = _family_group(candidate.family)
    speed = _speed_token(candidate.download_mbps)
    latency = _latency_token(candidate.latency_ms)
    rank_global = _rank_token(candidate.rank_global)
    rank_in_family = _rank_token(candidate.rank_in_family)
    display_label = " ".join(
        (rank_global, f"{flag}{country}", group, speed, latency, rank_in_family)
    )
    return _LabelParts(
        display_label=display_label,
        country=country,
        flag=flag,
        group=group,
        speed=speed,
        latency=latency,
        rank_global=rank_global,
        rank_in_family=rank_in_family,
    )


def _country_and_flag(country: str | None) -> tuple[str, str]:
    normalized = (country or "").strip().upper()
    if not _COUNTRY_RE.fullmatch(normalized):
        return _UNKNOWN_COUNTRY, _UNKNOWN_FLAG
    return normalized, _country_flag(normalized)


def _country_flag(country_code: str) -> str:
    return "".join(chr(ord(ch) + 127397) for ch in country_code)


def _family_group(family: str | None) -> str:
    normalized = (family or "").strip().lower()
    if not normalized:
        return _UNKNOWN_GROUP
    return _GROUP_BY_FAMILY.get(normalized, normalized.upper())


def _speed_token(download_mbps: Decimal | None) -> str:
    if download_mbps is None:
        return _UNKNOWN_SPEED
    quantized = download_mbps.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    compact = format(quantized, "f").rstrip("0").rstrip(".")
    if not compact:
        compact = "0"
    return f"{compact}M"


def _latency_token(latency_ms: int | None) -> str:
    if latency_ms is None:
        return _UNKNOWN_LATENCY
    if latency_ms < 0:
        return _UNKNOWN_LATENCY
    return f"{latency_ms}ms"


def _rank_token(rank: int | None) -> str:
    if rank is None:
        return _UNKNOWN_RANK
    return f"#{rank}"


def _relabel_raw_config(source_raw_config: str | None, label: str) -> _RelabelOutcome:
    if not source_raw_config:
        return _RelabelOutcome(
            export_raw_config="",
            relabel_strategy="skipped",
            error_code="raw_config_empty",
            error_text="Raw config is empty.",
        )

    try:
        split = urlsplit(source_raw_config)
    except ValueError as exc:
        return _RelabelOutcome(
            export_raw_config=source_raw_config,
            relabel_strategy="skipped",
            error_code="raw_config_parse_error",
            error_text=str(exc),
        )

    scheme = (split.scheme or "").strip().lower()
    if not scheme:
        return _RelabelOutcome(
            export_raw_config=source_raw_config,
            relabel_strategy="skipped",
            error_code="scheme_missing",
            error_text="Raw config scheme is missing.",
        )

    if scheme == "vmess":
        relabeled_vmess = _relabel_vmess_payload(source_raw_config, label)
        if relabeled_vmess is not None:
            return _RelabelOutcome(
                export_raw_config=relabeled_vmess,
                relabel_strategy="vmess_payload_ps",
            )

        relabeled_fragment = _relabel_url_like_fragment(source_raw_config, label)
        if relabeled_fragment is not None:
            return _RelabelOutcome(
                export_raw_config=relabeled_fragment,
                relabel_strategy="vmess_fragment_fallback",
                error_code="vmess_payload_decode_failed",
                error_text="Unable to decode vmess payload; relabeled URL fragment only.",
            )

        return _RelabelOutcome(
            export_raw_config=source_raw_config,
            relabel_strategy="skipped",
            error_code="vmess_relabel_failed",
            error_text="Failed to relabel vmess payload and fragment.",
        )

    relabeled_url = _relabel_url_like_fragment(source_raw_config, label)
    if relabeled_url is None:
        return _RelabelOutcome(
            export_raw_config=source_raw_config,
            relabel_strategy="skipped",
            error_code="url_fragment_relabel_failed",
            error_text="Failed to relabel URL fragment.",
        )

    return _RelabelOutcome(
        export_raw_config=relabeled_url,
        relabel_strategy="url_fragment",
    )


def _relabel_url_like_fragment(raw_config: str, label: str) -> str | None:
    try:
        split = urlsplit(raw_config)
    except ValueError:
        return None

    encoded_label = quote(label, safe="")
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, encoded_label))


def _relabel_vmess_payload(raw_config: str, label: str) -> str | None:
    if not raw_config.lower().startswith("vmess://"):
        return None

    vmess_tail = raw_config[len("vmess://") :]
    token, suffix = _split_vmess_token_suffix(vmess_tail)
    if not token:
        return None

    payload = _decode_vmess_payload(token)
    if payload is None:
        return None

    payload["ps"] = label
    encoded_payload = _encode_vmess_payload(payload, original_token=token)

    query_part, had_fragment = _parse_vmess_suffix(suffix)
    rebuilt = f"vmess://{encoded_payload}"
    if query_part:
        rebuilt = f"{rebuilt}?{query_part}"
    if had_fragment:
        rebuilt = f"{rebuilt}#{quote(label, safe='')}"
    return rebuilt


def _split_vmess_token_suffix(vmess_tail: str) -> tuple[str, str]:
    for index, ch in enumerate(vmess_tail):
        if ch in ("?", "#"):
            return vmess_tail[:index], vmess_tail[index:]
    return vmess_tail, ""


def _parse_vmess_suffix(suffix: str) -> tuple[str, bool]:
    if not suffix:
        return "", False

    if suffix.startswith("#"):
        return "", True

    if not suffix.startswith("?"):
        return "", "#" in suffix

    query_plus = suffix[1:]
    if "#" not in query_plus:
        return query_plus, False

    query_part, _fragment = query_plus.split("#", maxsplit=1)
    return query_part, True


def _decode_vmess_payload(token: str) -> dict[str, Any] | None:
    normalized = token.strip()
    if not normalized:
        return None

    padded = normalized.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)

    try:
        decoded = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if not _looks_like_vmess_payload(payload):
        return None
    return payload


def _looks_like_vmess_payload(payload: dict[str, Any]) -> bool:
    for field in _VMESS_REQUIRED_FIELDS:
        value = payload.get(field)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


def _encode_vmess_payload(payload: dict[str, Any], *, original_token: str) -> str:
    json_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded_bytes = json_payload.encode("utf-8")

    if "-" in original_token or "_" in original_token:
        encoded = base64.urlsafe_b64encode(encoded_bytes).decode("ascii")
    else:
        encoded = base64.b64encode(encoded_bytes).decode("ascii")

    if "=" not in original_token:
        encoded = encoded.rstrip("=")
    return encoded
