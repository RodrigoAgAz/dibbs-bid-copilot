"""Per-organization inputs for triage: the tenant's own quotes (cost model) and its suppliers' response history.

Both are loaded once per recommendation refresh (`RefreshContext`) and read for every line. Everything is scoped
to one organization by explicit filters, because services also run from jobs without a bound tenant session.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import (
    ApprovedSource,
    CatalogPrice,
    Channel,
    OrgSettings,
    PricePrediction,
    QuoteRequest,
    QuoteRequestStatus,
    SolicitationLine,
    SupplierQuote,
)
from app.services.landed_cost import price_per_issue_unit
from dibbs_ml.cost_model import QuoteObs, TenantCostModel

ANSWERED = (QuoteRequestStatus.replied, QuoteRequestStatus.parsed, QuoteRequestStatus.declined)
# Before a supplier has any history with this tenant: the chance an emailed request gets any answer, and how long
# it takes. Both are replaced by the tenant's own numbers as soon as it has sent requests.
PRIOR_RESPONSE_RATE = 0.30
PRIOR_RESPONSE_DAYS = 2.0
NO_EMAIL_RESPONSE_RATE = 0.02  # a candidate with no address on file: the broker would have to find one first


def tenant_quote_observations(db: Session, org_id: int) -> list[QuoteObs]:
    """Every usable quote the organization received, normalized to its line's unit of issue."""
    latest_pred = (
        select(PricePrediction.solicitation_line_id, func.max(PricePrediction.id).label("pid"))
        .group_by(PricePrediction.solicitation_line_id)
        .subquery()
    )
    rows = db.execute(
        select(SupplierQuote, SolicitationLine, PricePrediction.quantiles)
        .join(QuoteRequest, SupplierQuote.quote_request_id == QuoteRequest.id)
        .join(SolicitationLine, QuoteRequest.solicitation_line_id == SolicitationLine.id)
        .outerjoin(latest_pred, latest_pred.c.solicitation_line_id == SolicitationLine.id)
        .outerjoin(PricePrediction, PricePrediction.id == latest_pred.c.pid)
        .where(
            SupplierQuote.organization_id == org_id,
            QuoteRequest.organization_id == org_id,
            SupplierQuote.unit_price.is_not(None),
            SupplierQuote.declined.is_(False),
            (SupplierQuote.needs_review.is_(False)) | (SupplierQuote.reviewed_at.is_not(None)),
        )
    ).all()
    if not rows:
        return []
    nsns = {line.nsn for _, line, _ in rows}
    cages: dict[str, set[str]] = {}
    for nsn, cage in db.execute(select(ApprovedSource.nsn, ApprovedSource.cage_code).where(ApprovedSource.nsn.in_(nsns))).all():
        cages.setdefault(nsn, set()).add(str(cage).upper())
    out: list[QuoteObs] = []
    for q, line, quantiles in rows:
        if line.approved_source_only and not q.conforms_to_part_number:
            continue  # an alternate part's price says little about the approved part's cost
        price, problem, _ = price_per_issue_unit(q, line.unit_of_issue)
        if price is None or price <= 0:
            continue
        median = (quantiles or {}).get("median") if quantiles else None
        if (quantiles or {}).get("no_reference"):
            median = None
        out.append(QuoteObs(
            nsn=line.nsn, fsc=line.fsc, cages=frozenset(cages.get(line.nsn, ())), price=float(price),
            quantity=float(line.quantity or 1.0), when=(q.parsed_at or datetime.now(UTC)).date(),
            predicted_median=float(median) if median else None, quote_id=q.id,
        ))
    return out


def tenant_cost_model(db: Session, org_id: int, s: OrgSettings) -> TenantCostModel:
    return TenantCostModel(tenant_quote_observations(db, org_id), prior_fraction=s.provisional_cost_fraction, as_of=today_et())


@dataclass
class ResponseStats:
    """How this tenant's suppliers answer its emailed quote requests."""

    sent: dict[str, int] = field(default_factory=dict)  # per CAGE
    answered: dict[str, int] = field(default_factory=dict)
    hours: dict[str, list[float]] = field(default_factory=dict)
    total_sent: int = 0
    total_answered: int = 0
    all_hours: list[float] = field(default_factory=list)

    @property
    def overall_rate(self) -> float:
        # Smoothed toward the prior while the tenant has little history of its own.
        return (self.total_answered + 3 * PRIOR_RESPONSE_RATE) / (self.total_sent + 3)

    def rate(self, cage: str | None, has_email: bool) -> float:
        """P(this supplier answers an emailed request), smoothed toward the tenant's overall rate."""
        if not has_email:
            return NO_EMAIL_RESPONSE_RATE
        if not cage or cage not in self.sent:
            return self.overall_rate
        return (self.answered.get(cage, 0) + 2 * self.overall_rate) / (self.sent[cage] + 2)

    def median_days(self, cage: str | None = None) -> float:
        hrs = self.hours.get(cage or "", []) if cage else []
        if len(hrs) < 2:
            hrs = self.all_hours
        if not hrs:
            return PRIOR_RESPONSE_DAYS
        return max(statistics.median(hrs) / 24.0, 0.05)


def supplier_response_stats(db: Session, org_id: int) -> ResponseStats:
    st = ResponseStats()
    rows = db.execute(
        select(QuoteRequest.cage_code, QuoteRequest.status, QuoteRequest.sent_at, QuoteRequest.replied_at)
        .where(QuoteRequest.organization_id == org_id, QuoteRequest.channel == Channel.email,
               QuoteRequest.sent_at.is_not(None))
    ).all()
    for cage, status, sent_at, replied_at in rows:
        cage = (cage or "").upper()
        st.sent[cage] = st.sent.get(cage, 0) + 1
        st.total_sent += 1
        if status in ANSWERED or replied_at is not None:
            st.answered[cage] = st.answered.get(cage, 0) + 1
            st.total_answered += 1
            if replied_at is not None and sent_at is not None:
                h = max((replied_at - sent_at).total_seconds() / 3600.0, 0.0)
                st.hours.setdefault(cage, []).append(h)
                st.all_hours.append(h)
    return st


def tenant_catalog(db: Session, org_id: int) -> dict[tuple[str | None, str], list[float]]:
    """Imported distributor prices per (manufacturer CAGE, normalized part number), per unit of issue where the
    catalog states a basis (a price per 100 is divided by 100). Rows without a manufacturer CAGE match by part number
    alone under the key (None, pn)."""
    out: dict[tuple[str | None, str], list[float]] = {}
    for c in db.execute(select(CatalogPrice).where(CatalogPrice.organization_id == org_id)).scalars():
        price = float(c.unit_price) / float(c.price_basis_qty) if c.price_basis_qty else float(c.unit_price)
        out.setdefault((c.manufacturer_cage, c.part_number_norm), []).append(price)
        out.setdefault((None, c.part_number_norm), []).append(price)
    return out


def catalog_prices_for(catalog: dict, approved: list[tuple[str, str]]) -> list[float]:
    """Catalog prices for a line's approved (manufacturer CAGE, part number) pairs: exact manufacturer matches, else
    part-number-only rows."""
    from app.pipelines.enrichment import norm_pn

    out: list[float] = []
    for cage, pn in approved:
        key = ((cage or "").upper() or None, norm_pn(pn))
        hit = catalog.get(key) or catalog.get((None, key[1]))
        if hit:
            out.extend(hit)
    return out


@dataclass
class RefreshContext:
    """Per-organization inputs shared by every line of one recommendation refresh."""

    org_id: int
    settings: OrgSettings
    cost_model: TenantCostModel
    responses: ResponseStats
    catalog: dict = field(default_factory=dict)


def build_refresh_context(db: Session, org_id: int, s: OrgSettings) -> RefreshContext:
    return RefreshContext(org_id=org_id, settings=s, cost_model=tenant_cost_model(db, org_id, s),
                          responses=supplier_response_stats(db, org_id), catalog=tenant_catalog(db, org_id))
