"""Typed DTO models used by application layers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, IPvAnyAddress

from .enums import ProxyStatus, SourceFamily


class BaseDTO(BaseModel):
    """Base model configuration for DTOs."""

    model_config = ConfigDict(extra="forbid")


class Source(BaseDTO):
    id: UUID
    name: str
    url: str
    family: SourceFamily
    is_active: bool
    last_fetched_at: datetime | None = None
    last_checksum: str | None = None
    created_at: datetime
    updated_at: datetime


class SourceSnapshot(BaseDTO):
    id: UUID
    source_id: UUID
    fetched_at: datetime
    checksum: str
    raw_content: str


class ProxyCandidate(BaseDTO):
    id: UUID
    fingerprint: str
    raw_config: str
    protocol: str
    host: str | None = None
    port: int | None = None
    sni: str | None = None
    family: SourceFamily
    source_country_tag: str | None = None
    source_id: UUID | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    is_enabled: bool


class ProxyCheck(BaseDTO):
    id: UUID
    candidate_id: UUID
    checked_at: datetime
    connect_ok: bool
    connect_ms: int | None = None
    first_byte_ms: int | None = None
    download_mbps: Decimal | None = None
    exit_ip: IPvAnyAddress | None = None
    exit_country: str | None = None
    geo_match: bool | None = None
    error_code: str | None = None
    error_text: str | None = None


class ProxyState(BaseDTO):
    candidate_id: UUID
    status: ProxyStatus
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    current_country: str | None = None
    latency_ms: int | None = None
    download_mbps: Decimal | None = None
    stability_ratio: Decimal | None = None
    geo_confidence: Decimal | None = None
    freshness_score: Decimal | None = None
    final_score: Decimal | None = None
    rank_global: int | None = None
    rank_in_family: int | None = None
    rank_in_country: int | None = None
    updated_at: datetime

