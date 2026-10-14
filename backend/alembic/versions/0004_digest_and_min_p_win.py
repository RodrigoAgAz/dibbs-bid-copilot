"""daily digest opt-out per user; minimum win probability per org

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_digest", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("min_p_win", sa.Float(), nullable=False, server_default="0.2"))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("min_p_win")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_digest")
