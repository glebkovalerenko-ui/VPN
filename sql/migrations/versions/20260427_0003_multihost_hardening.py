"""add multihost verification diagnostics to proxy_checks

Revision ID: 20260427_0003
Revises: 20260425_0002
Create Date: 2026-04-27 16:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260427_0003"
down_revision = "20260425_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proxy_checks",
        sa.Column("user_targets_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("user_targets_successful", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("user_targets_success_ratio", sa.Numeric(precision=5, scale=4), nullable=True),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("critical_targets_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("critical_targets_successful", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("critical_targets_all_success", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("proxy_checks", sa.Column("multihost_failure_reason", sa.Text(), nullable=True))
    op.add_column(
        "proxy_checks",
        sa.Column("multihost_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proxy_checks", "multihost_summary")
    op.drop_column("proxy_checks", "multihost_failure_reason")
    op.drop_column("proxy_checks", "critical_targets_all_success")
    op.drop_column("proxy_checks", "critical_targets_successful")
    op.drop_column("proxy_checks", "critical_targets_total")
    op.drop_column("proxy_checks", "user_targets_success_ratio")
    op.drop_column("proxy_checks", "user_targets_successful")
    op.drop_column("proxy_checks", "user_targets_total")

