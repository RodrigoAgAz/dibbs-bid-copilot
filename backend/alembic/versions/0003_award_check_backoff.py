"""track the last award check per solicitation (award labeling backoff)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.models.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("solicitations") as batch:
        batch.add_column(sa.Column("award_checked_at", app.models.types.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("solicitations") as batch:
        batch.drop_column("award_checked_at")
