"""Fingerprint helpers for parser deduplication."""

from __future__ import annotations

import hashlib
import re

from .parsers import ParsedProxyLine
from .utils import normalize_host

_WS_RE = re.compile(r"\s+")


def build_fingerprint(candidate: ParsedProxyLine) -> str:
    """Build deterministic SHA-256 fingerprint from canonical proxy attributes."""
    protocol = candidate.protocol.strip().lower()
    host = normalize_host(candidate.host) or ""
    port = str(candidate.port) if candidate.port is not None else ""
    sni = (candidate.sni or "").strip().lower()
    auth = normalize_auth(candidate.canonical_auth)

    if any((host, port, sni, auth)):
        material = "|".join(("v1", protocol, host, port, sni, auth))
    else:
        material = "|".join(("v1", protocol, normalize_raw(candidate.raw_config)))

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_raw(raw_config: str) -> str:
    """Normalize fallback raw config string."""
    return _WS_RE.sub(" ", raw_config.strip())


def normalize_auth(value: str | None) -> str:
    """Normalize auth part while preserving semantic case where possible."""
    if value is None:
        return ""
    return value.strip()
