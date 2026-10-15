"""How DLA will evaluate this line, and what that does to the bid.

Source: DLA Master Solicitation for Automated Simplified Acquisitions, Part I, "Automated Award Evaluation
Information" (text of revision 46 read via a PTAC summary; the wording of revision 104's snippets and DLA's DIBBS FAQ
agree). Every automated RFQ PDF links the current revision; re-read it when DLA bumps the revision.

What the text says, and what the engine does with it:

* Automated solicitations (T; U = automated IDC) are awarded by DLA's Automated Evaluation program, which "uses price
  reasonableness logic and other automated filters" and "evaluates all qualified quotations on the basis of price
  alone". Past performance is not an automated factor. -> The calibrated price model stays the basis of P(win); the
  bid is capped at the band within which DLA has actually awarded automated buys in this supply class (95th
  percentile of winning price / inflation-adjusted previous award, from the award data: `FscCompetitors`).
* Qualified quotations are "bid without exception" and submitted on DIBBS. Exceptions that make a quote ineligible for
  automated award: an alternate product or exception to the item description, packaging, FOB terms, destination
  inspection on an origin-inspection buy, quantity, quantity variance, "None" for a higher-level quality requirement.
  -> Warnings when the chosen supplier quote implies one (non-conforming part number, freight terms that contradict
  the solicitation's FOB).
* Not exceptions: different delivery days ("delivery can become an evaluation factor"), a superseded part number,
  used/surplus items (+$200 evaluation factor), non-domestic end products on unrestricted buys (Buy American factor),
  origin inspection quoted on a destination buy (+$2,500 factor). -> Delivery is a feasibility check: supplier lead
  time plus the org's handling days must fit the required days; the factors are listed, not modelled as odds.
* First article / production lot testing: "not candidates for automated award", manually evaluated. -> Mode "manual".
* Fast Auto Evaluation (F icon, below the micro-purchase threshold): "may be awarded prior to the return date";
  evaluation starts "3 pm EST, 3 business days after the issue date and continues every day". -> The effective
  deadline for sourcing is the earlier of the return-by date and issue date + 3 business days.
* Late quotes are not considered once a fully automated evaluation has begun. Manual (Q) solicitations: the buyer
  weighs price, delivery and past performance; the price-only P(win) is an approximation there.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from app.core.timeutil import today_et
from app.models import OrgSettings, Solicitation, SolicitationLine, SupplierQuote
from dibbs_ml.competitors import FscCompetitors

FAST_EVAL_BUSINESS_DAYS = 3
ORIGIN_ON_DESTINATION_FACTOR = 2500.0
SURPLUS_FACTOR = 200.0
_FOB_ORIGIN = re.compile(r"fob\s*(origin|shipping point)", re.I)
_FOB_DEST = re.compile(r"fob\s*dest|prepaid|included|free\s*shipping", re.I)


@dataclass
class Evaluation:
    mode: str  # automated | fast_automated | aidc | manual
    price_only: bool
    manual_reasons: list[str] = field(default_factory=list)
    # The automated price band (bid ceiling) and where it comes from.
    cap_bid: float | None = None
    cap_ratio: float | None = None
    cap_reference: float | None = None
    cap_n: int = 0
    cap_source: str | None = None
    cap_binding: bool = False
    # Delivery feasibility of the quote the bid rests on.
    required_days: int | None = None
    quote_lead_days: int | None = None
    handling_days: int = 0
    delivery_feasible: bool | None = None
    # Deadline for sourcing: return-by, or earlier for fast auto evaluation.
    return_by: date | None = None
    effective_deadline: date | None = None
    effective_days_left: int | None = None
    # Things in the chosen quote that would make the bid ineligible for automated award, or add a factor.
    exceptions: list[str] = field(default_factory=list)
    factors: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("return_by", "effective_deadline"):
            d[k] = d[k].isoformat() if d[k] else None
        for k in ("cap_bid", "cap_ratio", "cap_reference"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        return d


def add_business_days(d: date, n: int) -> date:
    out = d
    while n > 0:
        out += timedelta(days=1)
        if out.weekday() < 5:
            n -= 1
    return out


def evaluation_mode(sol: Solicitation) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if sol.first_article:
        reasons.append("first article test required: not a candidate for automated award, evaluated manually")
    if (sol.type_code or "").upper() == "Q":
        reasons.append("manual solicitation (Q): a buyer evaluates price, delivery and past performance")
    if reasons:
        return "manual", reasons
    if sol.aidc or (sol.type_code or "").upper() == "U":
        return "aidc", reasons
    if sol.fast_award:
        return "fast_automated", reasons
    return "automated", reasons


def evaluate_line(
    line: SolicitationLine,
    sol: Solicitation,
    s: OrgSettings,
    reference_price: float | None,
    competitors: FscCompetitors | None,
    quote: SupplierQuote | None,
    has_approved_sources: bool,
    today: date | None = None,
) -> Evaluation:
    today = today or today_et()
    mode, manual_reasons = evaluation_mode(sol)
    ev = Evaluation(mode=mode, price_only=mode != "manual", manual_reasons=manual_reasons, return_by=sol.return_by_date)
    handling = int(getattr(s, "handling_days", 0) or 0)
    ev.handling_days = handling

    # Price band: only automated modes are filtered by DLA's price-reasonableness logic before a buyer sees them.
    if mode != "manual" and competitors is not None and competitors.automated_ratio_p95 and reference_price and reference_price > 0:
        ev.cap_ratio = float(competitors.automated_ratio_p95)
        ev.cap_reference = float(reference_price)
        ev.cap_bid = round(ev.cap_ratio * ev.cap_reference, 4)
        ev.cap_n = int(competitors.n_automated_ratios)
        ev.cap_source = (f"DLA has awarded automated FSC {line.fsc} buys at up to {ev.cap_ratio:.2f}x the inflation-adjusted "
                         f"previous award (95th percentile of {ev.cap_n:,} awards)")

    # Deadline: fast auto evaluation can award from 3 business days after issue.
    deadline = sol.return_by_date
    if mode == "fast_automated" and sol.issue_date:
        first_eval = add_business_days(sol.issue_date, FAST_EVAL_BUSINESS_DAYS)
        deadline = min(deadline, first_eval) if deadline else first_eval
        ev.notes.append("fast auto evaluation: may be awarded before the return date, from "
                        f"{first_eval:%b %d} (3 business days after issue); quote early")
    ev.effective_deadline = deadline
    ev.effective_days_left = (deadline - today).days if deadline else None

    # Delivery feasibility of the quote the bid rests on.
    ev.required_days = line.delivery_days
    if quote is not None:
        ev.quote_lead_days = quote.lead_time_days
        if quote.lead_time_days is not None and line.delivery_days:
            ev.delivery_feasible = quote.lead_time_days + handling <= line.delivery_days
            if not ev.delivery_feasible:
                ev.exceptions.append(
                    f"supplier lead time {quote.lead_time_days} d + {handling} d handling exceeds the required "
                    f"{line.delivery_days} d: a longer delivery is not an exception, but delivery becomes an evaluation factor")
        if not quote.conforms_to_part_number and has_approved_sources:
            ev.exceptions.append("the quoted part number is not an approved part: an alternate product is 'bid with exception' "
                                 "and ineligible for automated award")
        terms = quote.freight_terms or ""
        fob = (sol.fob or "").upper()
        if fob.startswith("DEST") and _FOB_ORIGIN.search(terms):
            ev.exceptions.append("the quote is FOB origin but the solicitation is FOB destination: price in freight and bid FOB "
                                 "destination, or the bid is ineligible for automated award")
        if fob.startswith("ORIG") and _FOB_DEST.search(terms) and "origin" not in terms.lower():
            ev.notes.append("the quote includes freight but the solicitation is FOB origin: bid FOB origin as solicited")
    if (sol.inspection_point or "").upper().startswith("DEST"):
        ev.factors.append({"label": "quoting origin inspection on this destination-inspection buy", "amount": ORIGIN_ON_DESTINATION_FACTOR})
    ev.factors.append({"label": "quoting unused former Government surplus", "amount": SURPLUS_FACTOR})
    if mode == "manual":
        ev.notes.append("the model's P(win) is price-only; on a manually evaluated buy the buyer also weighs delivery and past performance")
    elif mode == "aidc":
        ev.notes.append("automated indefinite-delivery contract: one-year contract, orders placed against the awarded price")
    return ev
