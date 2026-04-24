"""Ranking updates for Stage 8 scorer."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def recompute_proxy_state_ranks(session: Session) -> None:
    """Recompute global/family/country ranks from current final_score values."""
    session.execute(
        text(
            """
            UPDATE proxy_state
            SET
                rank_global = NULL,
                rank_in_family = NULL,
                rank_in_country = NULL
            """
        )
    )

    session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    ps.candidate_id,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            ps.final_score DESC,
                            ps.stability_ratio DESC NULLS LAST,
                            ps.last_success_at DESC NULLS LAST,
                            ps.candidate_id ASC
                    ) AS rank_value
                FROM proxy_state AS ps
                WHERE ps.final_score IS NOT NULL
                  AND ps.final_score > 0
            )
            UPDATE proxy_state AS ps
            SET rank_global = ranked.rank_value
            FROM ranked
            WHERE ps.candidate_id = ranked.candidate_id
            """
        )
    )

    session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    ps.candidate_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.family
                        ORDER BY
                            ps.final_score DESC,
                            ps.stability_ratio DESC NULLS LAST,
                            ps.last_success_at DESC NULLS LAST,
                            ps.candidate_id ASC
                    ) AS rank_value
                FROM proxy_state AS ps
                JOIN proxy_candidates AS c
                    ON c.id = ps.candidate_id
                WHERE ps.final_score IS NOT NULL
                  AND ps.final_score > 0
            )
            UPDATE proxy_state AS ps
            SET rank_in_family = ranked.rank_value
            FROM ranked
            WHERE ps.candidate_id = ranked.candidate_id
            """
        )
    )

    session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    ps.candidate_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY ps.current_country
                        ORDER BY
                            ps.final_score DESC,
                            ps.stability_ratio DESC NULLS LAST,
                            ps.last_success_at DESC NULLS LAST,
                            ps.candidate_id ASC
                    ) AS rank_value
                FROM proxy_state AS ps
                WHERE ps.current_country IS NOT NULL
                  AND ps.final_score IS NOT NULL
                  AND ps.final_score > 0
            )
            UPDATE proxy_state AS ps
            SET rank_in_country = ranked.rank_value
            FROM ranked
            WHERE ps.candidate_id = ranked.candidate_id
            """
        )
    )

