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
) -> LandedCost:
    qty = max(float(line.quantity or 1), 1.0)
    moq_note = None
    if quote is not None and quote.unit_price is not None:
        unit = float(quote.unit_price)
        provisional = False
        basis = f"supplier quote #{quote.id} ({quote.quote_request.cage_code if quote.quote_request else 'n/a'})"
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
    else:
        unit = float(predicted_median or 0.0) * s.provisional_cost_fraction
        effective = unit
        provisional = True
        basis = f"provisional: no supplier quote yet ({s.provisional_cost_fraction:.0%} of predicted median)"
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
