"""pursuit cost per line (triage score)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("pursuit_cost_per_line", sa.Float(), nullable=False, server_default="15"))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("pursuit_cost_per_line")
