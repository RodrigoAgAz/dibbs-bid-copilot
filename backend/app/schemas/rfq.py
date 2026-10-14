from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Flags(BaseModel):
    fast_award: bool = False
    approved_source_only: bool = False
    low_data: bool = False
    cold_start: bool = False
    provisional: bool = True
    first_article: bool = False
    aidc: bool = False
    # No NSN history and no supplier quote: no bid is recommended until a quote arrives.
    needs_quote: bool = False
    confidence: str = "low"  # high | medium | low | none (see services.recommendations.confidence_tier)


class QuoteStatusSummary(BaseModel):
    candidates: int = 0
    draft: int = 0
    sent: int = 0
    replied: int = 0
    parsed: int = 0
    declined: int = 0
    no_response: int = 0
    failed: int = 0
    needs_review: int = 0
    label: str = "not started"


class DashboardLine(BaseModel):
    line_id: int
    solicitation_number: str
    line_no: int
    type_code: str | None
    return_by_date: date | None
    days_left: int | None
    nsn: str
    fsc: str
    nomenclature: str | None
    quantity: float
    unit_of_issue: str | None
    set_aside: str | None
    predicted_median: float | None
    landed_cost: float | None
    recommended_bid: float | None
    conservative_bid: float | None
    aggressive_bid: float | None
    p_win: float | None
    expected_profit: float | None
    rank_score: float | None = None  # expected profit x confidence weight (default sort)
    no_bid: bool
    no_bid_reason: str | None
    actual_bid: float | None
    quote_status: QuoteStatusSummary
    flags: Flags


class DashboardSummary(BaseModel):
    as_of: date
    lines: int
    bid_lines: int
    no_bid_lines: int
    needs_quote_lines: int = 0
    total_expected_profit: float
    quotes_awaiting_reply: int
    needs_review: int
    last_ingest_at: datetime | None


class DashboardOut(BaseModel):
    summary: DashboardSummary
    lines: list[DashboardLine]


class AwardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    award_date: date
    contract_number: str
    awardee_cage: str | None
    awardee_name: str | None = None
    quantity: float
    unit_of_issue: str | None
    unit_price: float
    solicitation_number: str | None
    source: str


class ApprovedSourceOut(BaseModel):
    cage_code: str
    part_number: str
    company_name: str | None = None


class SupplierQuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    quote_request_id: int
    unit_price: float | None
    currency: str
    quoted_part_number: str | None
    min_order_qty: float | None
    lead_time_days: int | None
    quote_valid_until: date | None
    freight_terms: str | None
    freight_cost: float | None
    certs_offered: list[str]
    conforms_to_part_number: bool
    declined: bool
    extraction_confidence: float
    needs_review: bool
    review_reason: str | None
    reviewed_at: datetime | None
    source: str
    raw_text: str | None
    parsed_at: datetime


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    direction: str
    kind: str
    from_addr: str
    to_addr: str
    reply_to: str | None
    subject: str
    body: str
    created_at: datetime


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider: str
    started_at: datetime
    ended_at: datetime | None
    disclosure_played: bool
    transcript: str | None
    outcome: str | None


class QuoteRequestOut(BaseModel):
    id: int
    cage_code: str
    channel: str
    status: str
    candidate_reason: str | None
    part_number: str | None
    email_to: str | None
    email_subject: str | None
    email_body: str | None
    reply_address: str
    followup_count: int
    error: str | None
    sent_at: datetime | None
    replied_at: datetime | None
    quotes: list[SupplierQuoteOut] = Field(default_factory=list)
    emails: list[EmailOut] = Field(default_factory=list)
    calls: list[CallLogOut] = Field(default_factory=list)


class CandidateOut(BaseModel):
    cage_code: str
    company_name: str | None
    reason: str
    part_numbers: list[str]
    historical_awards: int
    supplier_contact_id: int | None
    contact_name: str | None
    email: str | None
    phone: str | None
    phone_type: str | None
    call_consent: bool
    callable: bool
    call_block_reasons: list[str]
    quote_requests: list[QuoteRequestOut]


class PredictionOut(BaseModel):
    levels: list[float]
    prices: list[float]
    median: float | None
    low_data: bool
    cold_start: bool
    n_prior_awards: int
    reference_price: float | None
    reference_type: str | None
    model: str | None
    contributions: list[dict[str, Any]]
    similar_awards: list[dict[str, Any]]
    created_at: datetime


class RecommendationOut(BaseModel):
    landed_cost_per_unit: float | None
    landed_breakdown: dict[str, Any] | None
    provisional: bool
    recommended_bid: float | None
    p_win_at_recommended: float | None
    expected_profit: float | None
    conservative_bid: float | None
    aggressive_bid: float | None
    no_bid: bool
    no_bid_reason: str | None
    explanation: dict[str, Any]
    actual_bid: float | None
    actual_bid_at: datetime | None
    updated_at: datetime


class LineDetail(BaseModel):
    id: int
    line_no: int
    nsn: str
    fsc: str
    niin: str
    nomenclature: str | None
    quantity: float
    unit_of_issue: str | None
    dod_standard_price: float | None
    amsc: str | None
    approved_source_only: bool
    delivery_days: int | None
    purchase_request: str | None
    prediction: PredictionOut | None
    recommendation: RecommendationOut | None
    history: list[AwardOut]
    approved_sources: list[ApprovedSourceOut]
    candidates: list[CandidateOut]
    outcome: dict[str, Any] | None


class SolicitationDetail(BaseModel):
    id: int
    solicitation_number: str
    type_code: str | None
    type_label: str
    issue_date: date | None
    return_by_date: date | None
    days_left: int | None
    buyer_code: str | None
    buyer_name: str | None
    buyer_email: str | None
    buyer_phone: str | None
    set_aside: str | None
    fast_award: bool
    aidc: bool
    tech_docs: bool
    inspection_point: str | None
    fob: str | None
    first_article: bool
    naics: str | None
    packaging: str | None
    status: str | None
    pdf_url: str | None
    dibbs_url: str
    lines: list[LineDetail]


class ActualBidIn(BaseModel):
    actual_bid: float | None = Field(default=None, ge=0)


class ManualQuoteIn(BaseModel):
    unit_price: float = Field(gt=0)
    quoted_part_number: str | None = Field(default=None, max_length=100)
    min_order_qty: float | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=2000)
    quote_valid_until: date | None = None
    freight_terms: str | None = Field(default=None, max_length=64)
    freight_cost: float | None = Field(default=None, ge=0)
    certs_offered: list[str] = Field(default_factory=list)


class QuotePatch(BaseModel):
    unit_price: float | None = Field(default=None, gt=0)
    quoted_part_number: str | None = Field(default=None, max_length=100)
    min_order_qty: float | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=2000)
    quote_valid_until: date | None = None
    freight_terms: str | None = Field(default=None, max_length=64)
    freight_cost: float | None = Field(default=None, ge=0)
    conforms_to_part_number: bool | None = None
    declined: bool | None = None
    mark_reviewed: bool = False


class QuoteRequestPatch(BaseModel):
    email_to: str | None = Field(default=None, max_length=320)
    email_subject: str | None = Field(default=None, max_length=300)
    email_body: str | None = Field(default=None, max_length=8000)


class NewQuoteRequestIn(BaseModel):
    cage_code: str = Field(min_length=5, max_length=5)
    email_to: str | None = Field(default=None, max_length=320)
    part_number: str | None = Field(default=None, max_length=100)


class DraftRequestsIn(BaseModel):
    cage_codes: list[str] | None = Field(default=None, max_length=50)


class SimulatedReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class CallIn(BaseModel):
    supplier_contact_id: int
    part_number: str | None = None


class ReviewItem(BaseModel):
    quote: SupplierQuoteOut
    organization_id: int
    organization_name: str | None = None
    cage_code: str
    solicitation_number: str
    nsn: str
    nomenclature: str | None
    quantity: float
    history_median: float | None


class HistoryRow(BaseModel):
    line_id: int
    solicitation_number: str
    return_by_date: date | None
    nsn: str
    nomenclature: str | None
    quantity: float
    recommended_bid: float | None
    actual_bid: float | None
    landed_cost: float | None
    no_bid: bool
    outcome: str  # won | lost | pending | no_award_data
    winning_price: float | None
    awardee_cage: str | None
    award_date: date | None


class HistoryStats(BaseModel):
    tracked: int
    with_actual_bid: int
    decided: int
    won: int
    lost: int
    win_rate: float | None
    realized_margin_pct: float | None
    recommendation_would_have_won: int
    recommendation_decided: int


class HistoryOut(BaseModel):
    stats: HistoryStats
    rows: list[HistoryRow]
