"""pilot imports: recommendations.source (engine | historical)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # "engine": computed by the recommendation engine. "historical": created by a pilot import for a line the tool
    # never priced (the firm's own past bid / cost / outcome); excluded from the dashboard, digest and portfolio.
    with op.batch_alter_table("recommendations") as batch:
        batch.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="engine"))


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.drop_column("source")
