"""handling days (delivery feasibility of supplier quotes)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("handling_days", sa.Integer(), nullable=False, server_default="3"))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("handling_days")
