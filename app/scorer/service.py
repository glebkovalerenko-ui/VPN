"""Stage 8 scorer orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import session_scope
from app.common.enums import ProxyStatus
from app.common.logging import get_logger
from app.common.settings import Settings, get_settings

from .aggregation import aggregate_candidate, fetch_candidates, fetch_recent_checks_by_candidate
from .models import ScoredState
from .ranking import recompute_proxy_state_ranks
from .scoring import score_candidate_state, validate_scorer_settings

logger = get_logger(__name__)


@dataclass(slots=True)
class ScorerCycleStats:
    """Execution metrics for one scorer run."""

    candidates_total: int = 0
    candidates_with_checks: int = 0
    active: int = 0
    degraded: int = 0
    dead: int = 0
    unknown: int = 0
    ranked_global: int = 0

    def to_log_extra(self) -> dict[str, int]:
        return {
            "candidates_total": self.candidates_total,
            "candidates_with_checks": self.candidates_with_checks,
            "active": self.active,
            "degraded": self.degraded,
            "dead": self.dead,
            "unknown": self.unknown,
            "ranked_global": self.ranked_global,
        }


def run_scorer_cycle(app_settings: Settings | None = None) -> ScorerCycleStats:
    """Run one Stage 8 scorer cycle and refresh proxy_state + ranks."""
    settings = app_settings or get_settings()
    validate_scorer_settings(settings)

    scored_at = datetime.now(timezone.utc)
    stats = ScorerCycleStats()

    with session_scope(settings) as session:
        candidates = fetch_candidates(session)
        recent_checks_by_candidate = fetch_recent_checks_by_candidate(
            session,
            recent_limit=settings.SCORER_RECENT_CHECKS_LIMIT,
        )

        scored_states: list[ScoredState] = []
        for candidate in candidates:
            recent_checks = recent_checks_by_candidate.get(candidate.id, [])
            aggregation = aggregate_candidate(candidate, recent_checks)
            scored_states.append(
                score_candidate_state(
                    aggregation,
                    settings,
                    scored_at=scored_at,
                )
            )

        upsert_proxy_states(session, scored_states)
        recompute_proxy_state_ranks(session)

        stats.candidates_total = len(scored_states)
        stats.candidates_with_checks = sum(
            1 for state in scored_states if state.last_check_at is not None
        )
        stats.active = sum(1 for state in scored_states if state.status == ProxyStatus.ACTIVE)
        stats.degraded = sum(1 for state in scored_states if state.status == ProxyStatus.DEGRADED)
        stats.dead = sum(1 for state in scored_states if state.status == ProxyStatus.DEAD)
        stats.unknown = sum(1 for state in scored_states if state.status == ProxyStatus.UNKNOWN)
        stats.ranked_global = _count_ranked_global(session)

    logger.info("Scorer cycle finished", extra=stats.to_log_extra())
    return stats


def upsert_proxy_states(session: Session, states: list[ScoredState]) -> None:
    """Upsert computed state rows without modifying proxy_checks history."""
    if not states:
        return

    session.execute(
        text(
            """
            INSERT INTO proxy_state (
                candidate_id,
                status,
                last_check_at,
                last_success_at,
                current_country,
                latency_ms,
                download_mbps,
                stability_ratio,
                geo_confidence,
                freshness_score,
                final_score,
                rank_global,
                rank_in_family,
                rank_in_country,
                updated_at
            )
            VALUES (
                :candidate_id,
                :status,
                :last_check_at,
                :last_success_at,
                :current_country,
                :latency_ms,
                :download_mbps,
                :stability_ratio,
                :geo_confidence,
                :freshness_score,
                :final_score,
                NULL,
                NULL,
                NULL,
                :updated_at
            )
            ON CONFLICT (candidate_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                last_check_at = EXCLUDED.last_check_at,
                last_success_at = EXCLUDED.last_success_at,
                current_country = EXCLUDED.current_country,
                latency_ms = EXCLUDED.latency_ms,
                download_mbps = EXCLUDED.download_mbps,
                stability_ratio = EXCLUDED.stability_ratio,
                geo_confidence = EXCLUDED.geo_confidence,
                freshness_score = EXCLUDED.freshness_score,
                final_score = EXCLUDED.final_score,
                rank_global = NULL,
                rank_in_family = NULL,
                rank_in_country = NULL,
                updated_at = EXCLUDED.updated_at
            """
        ),
        [
            {
                "candidate_id": state.candidate_id,
                "status": state.status.value,
                "last_check_at": state.last_check_at,
                "last_success_at": state.last_success_at,
                "current_country": state.current_country,
                "latency_ms": state.latency_ms,
                "download_mbps": state.download_mbps,
                "stability_ratio": state.stability_ratio,
                "geo_confidence": state.geo_confidence,
                "freshness_score": state.freshness_score,
                "final_score": state.final_score,
                "updated_at": state.updated_at,
            }
            for state in states
        ],
    )


def _count_ranked_global(session: Session) -> int:
    return int(
        session.execute(
            text(
                """
                SELECT COUNT(*) AS ranked_global
                FROM proxy_state
                WHERE rank_global IS NOT NULL
                """
            )
        ).scalar_one()
    )

