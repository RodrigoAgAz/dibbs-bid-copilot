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
    rank_score: float | None = None  # expected profit x confidence weight (default sort; quoted lines only)
    # Unquoted lines: profit if sourced x P(sourceable) x P(quote in time) x P(winnable) - pursuit cost
    triage_score: float | None = None
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
    quoted_expected_profit: float = 0.0  # from lines priced against real supplier quotes (the rest is estimated)
    quotes_awaiting_reply: int
    needs_review: int
    last_ingest_at: datetime | None


class DashboardOut(BaseModel):
    summary: DashboardSummary
    lines: list[DashboardLine]


class PortfolioOut(BaseModel):
    """The open book against the firm's limits (services/portfolio.py). Line ids name what each limit holds back."""

    quoted_lines: int
    expected_awards: float
    expected_profit: float
    capital_in_flight: float
    capital_if_all_won: float
    capital_days: float
    p_no_award: float
    working_capital_limit: float | None
    capital_hold: list[int]
    max_open_awards: int | None
    awards_hold: list[int]
    pursue_candidates: int
    pursuit_hours_needed: float
    weekly_sourcing_hours: float | None
    pursue_now: list[int]
    deferred_capacity: list[int]
    pursuit_cost_committed: float
    supplier_shares: list[dict[str, Any]]
    max_supplier_share: float
    concentrated_supplier: str | None
    notes: list[str]


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
    # False: derived from a contract total and not backed by verified prices; shown, but not used for pricing.
    verified: bool = True


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
    # What the supplier's price is per, and how many of the line's units of issue it covers (None = not stated)
    price_unit: str | None = None
    price_basis_qty: float | None = None
    # confirmed | inferred | unknown per fact: part_number, unit, lead_time, validity, freight
    evidence: dict[str, str] | None = None
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
    # Other NSNs in the same part-number family / from the same manufacturer and what DLA paid for them.
    similar_items: list[dict[str, Any]] = []
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
    actual_cost_per_unit: float | None = None
    actual_cost_at: datetime | None = None
    updated_at: datetime


class SnapshotOut(BaseModel):
    """One decision snapshot: what the tool showed (or the broker recorded) at that moment."""

    id: int
    kind: str
    engine_version: str | None
    created_at: datetime
    payload: dict[str, Any]


class ActualCostIn(BaseModel):
    actual_cost_per_unit: float | None = Field(default=None, ge=0)


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
    price_unit: str | None = Field(default=None, max_length=8)
    price_basis_qty: float | None = Field(default=None, gt=0)
    quoted_part_number: str | None = Field(default=None, max_length=100)
    min_order_qty: float | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=2000)
    quote_valid_until: date | None = None
    freight_terms: str | None = Field(default=None, max_length=64)
    freight_cost: float | None = Field(default=None, ge=0)
    certs_offered: list[str] = Field(default_factory=list)


class QuotePatch(BaseModel):
    unit_price: float | None = Field(default=None, gt=0)
    price_unit: str | None = Field(default=None, max_length=8)
    price_basis_qty: float | None = Field(default=None, gt=0)
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
    # The bid the tool recommended in the last snapshot taken before the award posted (None: no such snapshot)
    snapshot_bid: float | None = None
    actual_bid: float | None
    landed_cost: float | None
    actual_cost: float | None = None
    no_bid: bool
    outcome: str  # won | lost (you bid) | awarded (to someone else, no bid recorded) | pending | no_award_data
    winning_price: float | None
    # False: derived from a contract total and not backed by the NSN's verified prices (not used in the stats)
    winning_price_verified: bool = False
    awardee_cage: str | None
    award_date: date | None
    # Counted in "recommendation would win": a snapshot before the award, verified winning price, a bid recommended
    counted: bool = False


class HistoryStats(BaseModel):
    tracked: int
    with_actual_bid: int
    with_actual_cost: int = 0
    decided: int
    won: int
    lost: int
    win_rate: float | None
    # (bid - actual cost) / bid on wins where the broker recorded what they actually paid
    realized_margin_pct: float | None
    # (bid - estimated landed cost) / bid on wins: the margin the bid was priced for, not the one earned
    estimated_margin_pct: float | None = None
    recommendation_would_have_won: int
    recommendation_decided: int


class HistoryOut(BaseModel):
    stats: HistoryStats
    rows: list[HistoryRow]
