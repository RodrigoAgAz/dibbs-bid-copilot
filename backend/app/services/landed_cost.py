"""Landed cost per unit for a line, from the best conforming supplier quote (or a provisional estimate).

landed = supplier_unit_price + freight_per_unit + packaging_per_unit + inspection_allowance
         + first_article_allowance_per_unit + financing_cost + overhead
financing_cost = supplier_unit_price x cost_of_capital_pct x payment_terms_days / 365
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.models import OrgSettings, Solicitation, SolicitationLine, SupplierQuote

FREIGHT_INCLUDED = re.compile(r"(prepaid|included|free|fob\s*dest)", re.I)

# Count units of issue and how many "each" they stand for. A price quoted per one of these converts to a price per
# another; anything else (BX, RL, KT, FT vs EA) cannot be converted without the pack size.
COUNT_UNITS = {"EA": 1, "PC": 1, "HD": 100, "C": 100, "M": 1000, "DZ": 12, "GR": 144, "PR": 2}


def price_per_issue_unit(quote, unit_of_issue: str | None) -> tuple[float | None, str | None, str]:
    """The supplier's price per the line's unit of issue.

    Returns (price, problem, evidence): evidence is "confirmed" when the supplier stated the basis, "inferred" when
    the price is assumed to be per unit of issue, and problem explains why the quote cannot be used when price is None.
    """
    raw = quote.unit_price
    if raw is None:
        return None, "no unit price", "unknown"
    uoi = (unit_of_issue or "EA").upper()
    basis = getattr(quote, "price_basis_qty", None)
    unit = (getattr(quote, "price_unit", None) or "").upper() or None
    if basis:
        return float(raw) / float(basis), None, "confirmed"
    if unit is None:
        return float(raw), None, "inferred"
    if unit == uoi:
        return float(raw), None, "confirmed"
    if unit in COUNT_UNITS and uoi in COUNT_UNITS:
        return float(raw) * COUNT_UNITS[uoi] / COUNT_UNITS[unit], None, "confirmed"
    return None, f"quoted per {unit}, the solicitation is per {uoi}: cannot convert without the pack size", "confirmed"


@dataclass
class LandedCost:
    supplier_unit_price: float
    effective_unit_price: float  # after MOQ uplift
    freight_per_unit: float
    packaging_per_unit: float
    inspection_allowance: float
    first_article_allowance_per_unit: float
    financing_cost: float
    overhead: float
    landed: float
    provisional: bool
    basis: str
    quote_id: int | None = None
    moq_note: str | None = None

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def compute_landed_cost(
    line: SolicitationLine,
    sol: Solicitation,
    s: OrgSettings,
    quote: SupplierQuote | None,
    predicted_median: float | None,
    cost_estimate=None,
) -> LandedCost:
    """`cost_estimate` (dibbs_ml.cost_model.CostEstimate): the tenant cost model's supplier-price estimate, used for
    the provisional cost instead of the flat fraction of the predicted price when given."""
    qty = max(float(line.quantity or 1), 1.0)
    moq_note = None
    normalized = price_per_issue_unit(quote, getattr(line, "unit_of_issue", None))[0] if quote is not None else None
    if quote is not None and normalized is not None:
        unit = normalized
        provisional = False
        cage = quote.quote_request.cage_code if quote.quote_request else None
        basis = f"supplier quote from CAGE {cage}" if cage else "supplier quote"
        if getattr(quote, "price_basis_qty", None) and quote.price_basis_qty != 1:
            basis += f" ({quote.unit_price:g} per {quote.price_basis_qty:g} {getattr(line, 'unit_of_issue', None) or 'EA'})"
        effective = unit
        if quote.min_order_qty and quote.min_order_qty > qty:
            effective = unit * float(quote.min_order_qty) / qty
            moq_note = f"MOQ {quote.min_order_qty:g} > quantity {qty:g}: cost spread over the ordered quantity"
        if quote.freight_cost is not None:
            freight = float(quote.freight_cost) / qty
        elif quote.freight_terms and FREIGHT_INCLUDED.search(quote.freight_terms):
            freight = 0.0
        else:
            freight = s.default_freight_per_line / qty
    elif cost_estimate is not None and cost_estimate.median > 0:
        unit = float(cost_estimate.median)
        effective = unit
        provisional = True
        basis = f"estimate from {cost_estimate.basis}"
        freight = s.default_freight_per_line / qty
    else:
        unit = float(predicted_median or 0.0) * s.provisional_cost_fraction
        effective = unit
        provisional = True
        basis = f"estimate: no supplier quote yet ({s.provisional_cost_fraction:.0%} of the predicted median price)"
        freight = s.default_freight_per_line / qty

    packaging = s.packaging_cost_per_line / qty
    inspection = s.inspection_allowance_per_line / qty
    if (sol.inspection_point or "").upper().startswith("ORIGIN"):
        inspection += s.origin_inspection_allowance_per_line / qty
    first_article = s.first_article_allowance_per_line / qty if sol.first_article else 0.0
    financing = effective * s.cost_of_capital_pct * s.payment_terms_days / 365.0
    overhead = effective * s.default_overhead_pct
    landed = effective + freight + packaging + inspection + first_article + financing + overhead
    return LandedCost(
        supplier_unit_price=unit,
        effective_unit_price=effective,
        freight_per_unit=freight,
        packaging_per_unit=packaging,
        inspection_allowance=inspection,
        first_article_allowance_per_unit=first_article,
        financing_cost=financing,
        overhead=overhead,
        landed=landed,
        provisional=provisional,
        basis=basis,
        quote_id=quote.id if quote is not None and not provisional else None,
        moq_note=moq_note,
    )
