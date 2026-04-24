"""initial postgres schema for stage 1

Revision ID: 20260424_0001
Revises:
Create Date: 2026-04-24 18:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260424_0001"
down_revision = None
branch_labels = None
depends_on = None


FAMILY_CHECK = "family IN ('black', 'white_cidr', 'white_sni')"
STATUS_CHECK = "status IN ('active', 'degraded', 'dead', 'unknown')"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checksum", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(FAMILY_CHECK, name="ck_sources_family"),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("name", name="uq_sources_name"),
    )

    op.create_table(
        "source_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name="fk_source_snapshots_source_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_source_snapshots"),
    )
    op.execute(
        "CREATE INDEX ix_source_snapshots_source_id_fetched_at_desc "
        "ON source_snapshots (source_id, fetched_at DESC)"
    )

    op.create_table(
        "proxy_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("raw_config", sa.Text(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("sni", sa.Text(), nullable=True),
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("source_country_tag", sa.Text(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(FAMILY_CHECK, name="ck_proxy_candidates_family"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name="fk_proxy_candidates_source_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_proxy_candidates"),
        sa.UniqueConstraint("fingerprint", name="uq_proxy_candidates_fingerprint"),
    )
    op.create_index(
        "ix_proxy_candidates_family_is_enabled",
        "proxy_candidates",
        ["family", "is_enabled"],
        unique=False,
    )
    op.execute("CREATE INDEX ix_proxy_candidates_last_seen_at_desc ON proxy_candidates (last_seen_at DESC)")

    op.create_table(
        "proxy_checks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connect_ok", sa.Boolean(), nullable=False),
        sa.Column("connect_ms", sa.Integer(), nullable=True),
        sa.Column("first_byte_ms", sa.Integer(), nullable=True),
        sa.Column("download_mbps", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("exit_ip", postgresql.INET(), nullable=True),
        sa.Column("exit_country", sa.Text(), nullable=True),
        sa.Column("geo_match", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["proxy_candidates.id"], name="fk_proxy_checks_candidate_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_proxy_checks"),
    )
    op.execute(
        "CREATE INDEX ix_proxy_checks_candidate_id_checked_at_desc "
        "ON proxy_checks (candidate_id, checked_at DESC)"
    )
    op.execute("CREATE INDEX ix_proxy_checks_checked_at_desc ON proxy_checks (checked_at DESC)")

    op.create_table(
        "proxy_state",
        sa.Column("candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_country", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("download_mbps", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("stability_ratio", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("geo_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("freshness_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("final_score", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("rank_global", sa.Integer(), nullable=True),
        sa.Column("rank_in_family", sa.Integer(), nullable=True),
        sa.Column("rank_in_country", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(STATUS_CHECK, name="ck_proxy_state_status"),
        sa.ForeignKeyConstraint(["candidate_id"], ["proxy_candidates.id"], name="fk_proxy_state_candidate_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("candidate_id", name="pk_proxy_state"),
    )
    op.execute("CREATE INDEX ix_proxy_state_status_final_score_desc ON proxy_state (status, final_score DESC)")
    op.execute(
        "CREATE INDEX ix_proxy_state_current_country_final_score_desc "
        "ON proxy_state (current_country, final_score DESC)"
    )


def downgrade() -> None:
    op.drop_table("proxy_state")
    op.drop_table("proxy_checks")
    op.drop_table("proxy_candidates")
    op.drop_table("source_snapshots")
    op.drop_table("sources")
