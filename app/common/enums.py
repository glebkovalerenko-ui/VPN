"""Shared enums for typed application models."""

from enum import StrEnum


class SourceFamily(StrEnum):
    BLACK = "black"
    WHITE_CIDR = "white_cidr"
    WHITE_SNI = "white_sni"


class ProxyStatus(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DEAD = "dead"
    UNKNOWN = "unknown"

