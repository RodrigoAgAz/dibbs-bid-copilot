"""store model artifacts in the database

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_runs") as batch:
        batch.add_column(sa.Column("artifact_blob", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_runs") as batch:
        batch.drop_column("artifact_blob")
