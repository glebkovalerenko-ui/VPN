"""Pydantic response models for Stage 11 HTTP API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.common.enums import ProxyStatus, SourceFamily


class APIBaseModel(BaseModel):
    """Common API schema configuration."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(APIBaseModel):
    status: str
    db: str
    output_dir_exists: bool
    manifest_exists: bool
    db_error: str | None = None


class StateCandidateItem(APIBaseModel):
    candidate_id: str
    status: ProxyStatus
    family: SourceFamily
    protocol: str
    host: str | None = None
    port: int | None = None
    sni: str | None = None
    is_enabled: bool
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
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    updated_at: datetime


class StateTopResponse(APIBaseModel):
    limit: int
    status: ProxyStatus | None = None
    family: SourceFamily | None = None
    country: str | None = None
    only_positive_score: bool
    items: list[StateCandidateItem]


class StateCandidatesResponse(APIBaseModel):
    limit: int
    offset: int
    total: int
    status: ProxyStatus | None = None
    family: SourceFamily | None = None
    protocol: str | None = None
    country: str | None = None
    enabled_only: bool
    min_final_score: Decimal | None = None
    only_positive_score: bool
    items: list[StateCandidateItem]
