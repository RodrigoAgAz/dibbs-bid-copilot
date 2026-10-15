"""Model outputs, recommendations, model registry and pipeline bookkeeping."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned
from app.models.types import JSONType, UTCDateTime, utcnow


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    model_type: Mapped[str] = mapped_column(String(32), default="lgbm_quantile")
    train_window: Mapped[str] = mapped_column(String(64))
    test_window: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The joblib artifact itself, so every service (API, worker, scheduler) can load the model
    # without a shared filesystem (e.g. on Railway). The file at artifact_path is a local cache.
    artifact_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PricePrediction(Base):
    """Predicted distribution of the winning unit price for a line (global, not tenant-owned)."""

    __tablename__ = "price_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solicitation_line_id: Mapped[int] = mapped_column(
        ForeignKey("solicitation_lines.id", ondelete="CASCADE"), index=True
    )
    model_run_id: Mapped[int | None] = mapped_column(ForeignKey("model_runs.id", ondelete="SET NULL"), nullable=True)
    # {"levels": [...], "prices": [...], "median": x, "low_data": bool, "reference_price": x,
    #  "reference_type": "...", "contributions": [...], "similar_awards": [...]}
    quantiles: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Recommendation(Base, TenantOwned):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("organization_id", "solicitation_line_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    solicitation_line_id: Mapped[int] = mapped_column(
        ForeignKey("solicitation_lines.id", ondelete="CASCADE"), index=True
    )
    price_prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("price_predictions.id", ondelete="SET NULL"), nullable=True
    )
    best_quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_quotes.id", ondelete="SET NULL"), nullable=True
    )
    landed_cost_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    landed_breakdown: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    recommended_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_win_at_recommended: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    conservative_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    aggressive_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_bid: Mapped[bool] = mapped_column(Boolean, default=False)
    no_bid_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    explanation: Mapped[dict] = mapped_column(JSONType, default=dict)
    # "engine": computed by the recommendation engine. "historical": created by a pilot import (pipelines/pilot_import.py)
    # for a line the tool never priced, so the firm's own bid / cost / outcome can be kept; such rows carry no
    # recommendation and are excluded from the dashboard, digest and portfolio.
    source: Mapped[str] = mapped_column(String(16), default="engine", server_default="engine")
    actual_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_bid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # What the broker actually paid per unit after the award (purchase order + freight etc.). Only this makes a
    # "realized" margin; the landed cost above is the estimate the bid was priced against.
    actual_cost_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class RecommendationSnapshot(Base, TenantOwned):
    """Append-only record of what the broker was shown, taken whenever a recommendation's decision-relevant values
    change and when the broker records a bid or a cost. Recommendations themselves are upserted in place, so without
    this nothing could say what the tool recommended before an award posted."""

    __tablename__ = "recommendation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    solicitation_line_id: Mapped[int] = mapped_column(
        ForeignKey("solicitation_lines.id", ondelete="CASCADE"), index=True
    )
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="recommendation")  # recommendation | actual_bid | actual_cost
    engine_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | success | partial | failed
    rows_ingested: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSONType, default=list)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


class IngestCheckpoint(Base):
    """Resumability for backfill / daily ingest: one row per (source, key) unit of work."""

    __tablename__ = "ingest_checkpoints"
    __table_args__ = (UniqueConstraint("source", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="done")  # done | failed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
