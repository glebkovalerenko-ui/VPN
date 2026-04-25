"""add speed measurement diagnostics

Revision ID: 20260425_0002
Revises: 20260424_0001
Create Date: 2026-04-25 20:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260425_0002"
down_revision = "20260424_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxy_checks", sa.Column("speed_error_code", sa.Text(), nullable=True))
    op.add_column("proxy_checks", sa.Column("speed_failure_reason", sa.Text(), nullable=True))
    op.add_column("proxy_checks", sa.Column("speed_error_text", sa.Text(), nullable=True))
    op.add_column("proxy_checks", sa.Column("speed_endpoint_url", sa.Text(), nullable=True))
    op.add_column(
        "proxy_checks",
        sa.Column("speed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "proxy_checks",
        sa.Column("speed_successes", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("proxy_checks", "speed_successes")
    op.drop_column("proxy_checks", "speed_attempts")
    op.drop_column("proxy_checks", "speed_endpoint_url")
    op.drop_column("proxy_checks", "speed_error_text")
    op.drop_column("proxy_checks", "speed_failure_reason")
    op.drop_column("proxy_checks", "speed_error_code")
