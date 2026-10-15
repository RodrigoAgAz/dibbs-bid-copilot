"""supplier quote pricing unit + evidence states; actual cost on recommendations; decision snapshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("supplier_quotes") as batch:
        batch.add_column(sa.Column("price_unit", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("price_basis_qty", sa.Float(), nullable=True))
        batch.add_column(sa.Column("evidence", JSON, nullable=True))
    with op.batch_alter_table("recommendations") as batch:
        batch.add_column(sa.Column("actual_cost_per_unit", sa.Float(), nullable=True))
        batch.add_column(sa.Column("actual_cost_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "recommendation_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("solicitation_line_id", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("engine_version", sa.String(length=16), nullable=True),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name=op.f("fk_recommendation_snapshots_organization_id_organizations")),
        sa.ForeignKeyConstraint(["solicitation_line_id"], ["solicitation_lines.id"], ondelete="CASCADE",
                                name=op.f("fk_recommendation_snapshots_solicitation_line_id_solicitation_lines")),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="SET NULL",
                                name=op.f("fk_recommendation_snapshots_recommendation_id_recommendations")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_snapshots")),
    )
    op.create_index(op.f("ix_recommendation_snapshots_organization_id"), "recommendation_snapshots", ["organization_id"])
    op.create_index(op.f("ix_recommendation_snapshots_solicitation_line_id"), "recommendation_snapshots",
                    ["solicitation_line_id"])
    op.create_index(op.f("ix_recommendation_snapshots_recommendation_id"), "recommendation_snapshots",
                    ["recommendation_id"])
    op.create_index(op.f("ix_recommendation_snapshots_created_at"), "recommendation_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_recommendation_snapshots_created_at"), table_name="recommendation_snapshots")
    op.drop_index(op.f("ix_recommendation_snapshots_recommendation_id"), table_name="recommendation_snapshots")
    op.drop_index(op.f("ix_recommendation_snapshots_solicitation_line_id"), table_name="recommendation_snapshots")
    op.drop_index(op.f("ix_recommendation_snapshots_organization_id"), table_name="recommendation_snapshots")
    op.drop_table("recommendation_snapshots")
    with op.batch_alter_table("recommendations") as batch:
        batch.drop_column("actual_cost_at")
        batch.drop_column("actual_cost_per_unit")
    with op.batch_alter_table("supplier_quotes") as batch:
        batch.drop_column("evidence")
        batch.drop_column("price_basis_qty")
        batch.drop_column("price_unit")
