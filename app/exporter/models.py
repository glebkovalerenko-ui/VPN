"""Domain models used by Stage 9 exporter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class ExportCandidate:
    """Joined candidate/state row required for export selection."""

    candidate_id: str
    status: str
    family: str
    raw_config: str
    host: str | None
    fingerprint: str | None
    current_country: str | None
    final_score: Decimal
    stability_ratio: Decimal | None
    last_success_at: datetime | None
    rank_global: int | None
    rank_in_family: int | None
    rank_in_country: int | None
