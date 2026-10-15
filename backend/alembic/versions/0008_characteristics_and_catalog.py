"""item characteristics (PUB LOG) and imported distributor catalog prices

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_characteristics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nsn", sa.String(length=16), nullable=False),
        sa.Column("mrc", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("value", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_characteristics")),
        sa.UniqueConstraint("nsn", "mrc", name=op.f("uq_item_characteristics_nsn_mrc")),
    )
    op.create_index(op.f("ix_item_characteristics_nsn"), "item_characteristics", ["nsn"])
    op.create_table(
        "catalog_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("supplier_cage", sa.String(length=5), nullable=False),
        sa.Column("manufacturer_cage", sa.String(length=5), nullable=True),
        sa.Column("part_number", sa.String(length=100), nullable=False),
        sa.Column("part_number_norm", sa.String(length=100), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_unit", sa.String(length=8), nullable=True),
        sa.Column("price_basis_qty", sa.Float(), nullable=True),
        sa.Column("min_order_qty", sa.Float(), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=True),
        sa.Column("source_file", sa.String(length=300), nullable=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name=op.f("fk_catalog_prices_organization_id_organizations")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_prices")),
        sa.UniqueConstraint("organization_id", "supplier_cage", "manufacturer_cage", "part_number_norm",
                            name=op.f("uq_catalog_prices_organization_id_supplier_cage_manufacturer_cage_part_number_norm")),
    )
    op.create_index(op.f("ix_catalog_prices_organization_id"), "catalog_prices", ["organization_id"])
    op.create_index(op.f("ix_catalog_prices_supplier_cage"), "catalog_prices", ["supplier_cage"])
    op.create_index(op.f("ix_catalog_prices_manufacturer_cage"), "catalog_prices", ["manufacturer_cage"])
    op.create_index(op.f("ix_catalog_prices_part_number_norm"), "catalog_prices", ["part_number_norm"])


def downgrade() -> None:
    for ix in ("part_number_norm", "manufacturer_cage", "supplier_cage", "organization_id"):
        op.drop_index(op.f(f"ix_catalog_prices_{ix}"), table_name="catalog_prices")
    op.drop_table("catalog_prices")
    op.drop_index(op.f("ix_item_characteristics_nsn"), table_name="item_characteristics")
    op.drop_table("item_characteristics")
