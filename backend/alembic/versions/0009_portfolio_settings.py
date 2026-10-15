"""portfolio and risk settings: working capital, open awards, sourcing hours, supplier share, fulfilment losses

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("working_capital_limit", sa.Float(), nullable=True))
        batch.add_column(sa.Column("max_open_awards", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("weekly_sourcing_hours", sa.Float(), nullable=True))
        batch.add_column(sa.Column("pursuit_hours_per_line", sa.Float(), nullable=False, server_default="0.33"))
        batch.add_column(sa.Column("max_supplier_share", sa.Float(), nullable=False, server_default="0.5"))
        batch.add_column(sa.Column("fulfilment_loss_pct", sa.Float(), nullable=False, server_default="0.02"))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        for col in ("fulfilment_loss_pct", "max_supplier_share", "pursuit_hours_per_line", "weekly_sourcing_hours",
                    "max_open_awards", "working_capital_limit"):
            batch.drop_column(col)
