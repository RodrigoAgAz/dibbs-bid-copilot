"""Public DIBBS data: solicitations, lines, NSNs, approved sources, award history, companies.

These tables are global (not tenant-owned): they hold public data shared by all organizations.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import JSONType, UTCDateTime, utcnow


class Solicitation(Base):
    __tablename__ = "solicitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solicitation_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # 9th character of the solicitation number: T/U = automated small purchase, Q = manual
    type_code: Mapped[str | None] = mapped_column(String(1), nullable=True, index=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    return_by_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    buyer_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    set_aside: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fast_award: Mapped[bool] = mapped_column(Boolean, default=False)
    aidc: Mapped[bool] = mapped_column(Boolean, default=False)
    tech_docs: Mapped[bool] = mapped_column(Boolean, default=False)
    inspection_point: Mapped[str | None] = mapped_column(String(32), nullable=True)  # origin | destination
    fob: Mapped[str | None] = mapped_column(String(32), nullable=True)  # origin | destination
    first_article: Mapped[bool] = mapped_column(Boolean, default=False)
    naics: Mapped[str | None] = mapped_column(String(10), nullable=True)
    packaging: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    details_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Last record-page check for an award (award labeling backs off as the solicitation ages).
    award_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    lines: Mapped[list[SolicitationLine]] = relationship(
        back_populates="solicitation", cascade="all, delete-orphan", order_by="SolicitationLine.line_no"
    )


class SolicitationLine(Base):
    __tablename__ = "solicitation_lines"
    __table_args__ = (UniqueConstraint("solicitation_id", "line_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solicitation_id: Mapped[int] = mapped_column(ForeignKey("solicitations.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    nsn: Mapped[str] = mapped_column(String(16), index=True)  # 13 digits, no hyphens
    fsc: Mapped[str] = mapped_column(String(4), index=True)
    niin: Mapped[str] = mapped_column(String(9))
    nomenclature: Mapped[str | None] = mapped_column(String(300), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit_of_issue: Mapped[str | None] = mapped_column(String(4), nullable=True)
    dod_standard_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amsc: Mapped[str | None] = mapped_column(String(2), nullable=True)
    approved_source_only: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_request: Mapped[str | None] = mapped_column(String(32), nullable=True)

    solicitation: Mapped[Solicitation] = relationship(back_populates="lines")


class NsnItem(Base):
    __tablename__ = "nsn_items"

    nsn: Mapped[str] = mapped_column(String(16), primary_key=True)
    fsc: Mapped[str] = mapped_column(String(4), index=True)
    nomenclature: Mapped[str | None] = mapped_column(String(300), nullable=True)
    unit_of_issue: Mapped[str | None] = mapped_column(String(4), nullable=True)
    amsc: Mapped[str | None] = mapped_column(String(2), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ItemCharacteristic(Base):
    """One technical characteristic of an NSN from DLA's public FLIS / PUB LOG data (material, thread size, ...).

    Loaded from an exported file (`app/pipelines/characteristics.py`); the live format is documented as UNVERIFIED in
    docs/data_sources.md. Global public data.
    """

    __tablename__ = "item_characteristics"
    __table_args__ = (UniqueConstraint("nsn", "mrc"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nsn: Mapped[str] = mapped_column(String(16), index=True)
    mrc: Mapped[str] = mapped_column(String(8))  # Master Requirement Code (e.g. MATT material, AAGR thread size)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    value: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(32), default="pub_log")
    loaded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ApprovedSource(Base):
    __tablename__ = "approved_sources"
    __table_args__ = (UniqueConstraint("nsn", "cage_code", "part_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nsn: Mapped[str] = mapped_column(String(16), index=True)
    cage_code: Mapped[str] = mapped_column(String(5), index=True)
    part_number: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(32), default="rfq_pdf")


class AwardHistory(Base):
    __tablename__ = "award_history"
    __table_args__ = (UniqueConstraint("nsn", "contract_number", "award_date", "quantity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nsn: Mapped[str] = mapped_column(String(16), index=True)
    award_date: Mapped[date] = mapped_column(Date, index=True)
    contract_number: Mapped[str] = mapped_column(String(40))
    awardee_cage: Mapped[str | None] = mapped_column(String(5), nullable=True, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    unit_of_issue: Mapped[str | None] = mapped_column(String(4), nullable=True)
    unit_price: Mapped[float] = mapped_column(Float)
    total_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="rfq_pdf")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Company(Base):
    __tablename__ = "companies"

    cage_code: Mapped[str] = mapped_column(String(5), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sam_uei: Mapped[str | None] = mapped_column(String(12), nullable=True)
    is_manufacturer_guess: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
