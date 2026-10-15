"""Per-tenant landed-cost model: what a broker's supplier price is likely to be before anyone has quoted.

The winning price is public; the broker's cost is private but predictable from the broker's OWN past quotes.
Each estimate comes from the first tier with enough observations, and says which tier it came from:

    nsn    the tenant's quotes for this NSN (quantity-adjusted, recent first)
    cage   the tenant's quotes for other NSNs made by the same approved-source manufacturer(s)
    fsc    the tenant's quotes in the same supply class, as a ratio to the predicted winning price
    org    all the tenant's quotes, as a ratio to the predicted winning price
    prior  the organization's provisional cost fraction with a wide spread: an assumption, labelled as such

Everything is per tenant. Nothing here pools quotes across organizations (see DECISIONS.md).
Pure Python: the backend loads the observations (app/services/cost_model.py) and calls `TenantCostModel`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np
from scipy.stats import norm

from dibbs_ml.items import QTY_ELASTICITY

TIERS = ("nsn", "catalog", "cage", "fsc", "org", "prior")
MIN_OBS = {"nsn": 1, "catalog": 1, "cage": 2, "fsc": 3, "org": 5}
# Floors on the log-space spread: one quote for an NSN is not certainty, and ratios drift between items. A catalog
# price is a current list price for the exact part, but the broker may negotiate below it or pay freight above it.
SIGMA_FLOOR = {"nsn": 0.12, "catalog": 0.10, "cage": 0.25, "fsc": 0.30, "org": 0.35, "prior": 0.45}
RECENCY_MONTHS = 24
Z10, Z90 = norm.ppf(0.1), norm.ppf(0.9)


@dataclass(frozen=True)
class QuoteObs:
    """One usable supplier quote the tenant received, normalized to the line's unit of issue."""

    nsn: str
    fsc: str
    cages: frozenset[str]  # approved-source manufacturers of the quoted NSN (empty when unknown)
    price: float  # supplier price per unit of issue
    quantity: float
    when: date
    predicted_median: float | None  # the predicted winning price of that line (None: no reference)
    quote_id: int | None = None


@dataclass
class CostEstimate:
    median: float
    sigma: float  # log-space spread
    tier: str
    n: int
    basis: str  # one line for the UI, e.g. "your 3 quotes for this NSN"
    ratio: float | None = None  # cost / predicted winning price for ratio tiers (and the prior)
    quote_ids: list[int] = field(default_factory=list)

    @property
    def q10(self) -> float:
        return float(self.median * math.exp(self.sigma * Z10))

    @property
    def q90(self) -> float:
        return float(self.median * math.exp(self.sigma * Z90))

    def p_at_or_below(self, x: float) -> float:
        """P(supplier price <= x) under the lognormal estimate: the chance a quote comes in at or under a target."""
        if x <= 0 or self.median <= 0:
            return 0.0
        return float(norm.cdf((math.log(x) - math.log(self.median)) / self.sigma))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(q10=round(self.q10, 4), q90=round(self.q90, 4), median=round(self.median, 4), sigma=round(self.sigma, 4))
        return d


def _log_stats(values: np.ndarray, floor: float) -> tuple[float, float]:
    logs = np.log(np.clip(values, 1e-9, None))
    sigma = float(np.std(logs, ddof=1)) if logs.size >= 2 else 0.0
    return float(np.median(logs)), max(sigma, floor)


class TenantCostModel:
    def __init__(self, observations: list[QuoteObs], prior_fraction: float = 0.70, as_of: date | None = None) -> None:
        self.as_of = as_of or date.today()
        since = self.as_of - timedelta(days=int(RECENCY_MONTHS * 30.4))
        # Older quotes still count when nothing newer exists, but recent ones are preferred per tier.
        self.obs = sorted(observations, key=lambda o: o.when)
        self.recent = [o for o in self.obs if o.when >= since] or self.obs
        self.prior_fraction = float(prior_fraction)

    # ------------------------------------------------------------------ tiers
    def _nsn(self, nsn: str, quantity: float) -> tuple[np.ndarray, list[int]] | None:
        rows = [o for o in self.recent if o.nsn == nsn]
        if len(rows) < MIN_OBS["nsn"]:
            return None
        q = max(float(quantity or 1.0), 1e-9)
        # Within-NSN quantity elasticity: a quote for 10 says less about a buy of 1,000 than one for 800 does.
        adj = np.array([o.price * (q / max(o.quantity, 1e-9)) ** QTY_ELASTICITY for o in rows])
        return adj, [o.quote_id for o in rows if o.quote_id is not None]

    def _ratio_rows(self, pred) -> list[QuoteObs]:
        return [o for o in self.recent if pred(o) and o.predicted_median and o.predicted_median > 0 and o.price > 0]

    def estimate(self, nsn: str, fsc: str, cages: frozenset[str] | set[str] | None, predicted_median: float | None,
                 quantity: float = 1.0, exclude_quote_id: int | None = None,
                 catalog_prices: list[float] | None = None) -> CostEstimate | None:
        """The first tier with enough observations. None when nothing can be said (no predicted price, no NSN quotes,
        no catalog price). `catalog_prices`: current list prices (per unit of issue) the tenant imported for the
        line's exact approved part numbers."""
        model = self if exclude_quote_id is None else TenantCostModel(
            [o for o in self.obs if o.quote_id != exclude_quote_id], self.prior_fraction, self.as_of)
        got = model._nsn(nsn, quantity)
        if got is not None:
            adj, ids = got
            mu, sigma = _log_stats(adj, SIGMA_FLOOR["nsn"])
            n = len(adj)
            return CostEstimate(math.exp(mu), sigma, "nsn", n, f"your {n} quote{'s' if n > 1 else ''} for this NSN",
                                ratio=(math.exp(mu) / predicted_median) if predicted_median else None, quote_ids=ids)
        cat = [float(p) for p in (catalog_prices or []) if p and p > 0]
        if len(cat) >= MIN_OBS["catalog"]:
            mu, sigma = _log_stats(np.array(cat), SIGMA_FLOOR["catalog"])
            n = len(cat)
            return CostEstimate(math.exp(mu), sigma, "catalog", n,
                                f"{n} imported catalog price{'s' if n > 1 else ''} for the approved part number",
                                ratio=(math.exp(mu) / predicted_median) if predicted_median else None)
        if not predicted_median or predicted_median <= 0:
            return None
        cages = frozenset(c.upper() for c in (cages or ()))
        tiers: list[tuple[str, list[QuoteObs], str]] = []
        if cages:
            rows = model._ratio_rows(lambda o: o.nsn != nsn and bool(o.cages & cages))
            tiers.append(("cage", rows, "your {n} quotes for other parts by the same manufacturer"))
        tiers.append(("fsc", model._ratio_rows(lambda o: o.fsc == fsc and o.nsn != nsn), "your {n} quotes in FSC " + fsc))
        tiers.append(("org", model._ratio_rows(lambda o: o.nsn != nsn), "your {n} quotes across all supply classes"))
        for tier, rows, text in tiers:
            if len(rows) >= MIN_OBS[tier]:
                ratios = np.array([o.price / o.predicted_median for o in rows])
                mu, sigma = _log_stats(ratios, SIGMA_FLOOR[tier])
                r = math.exp(mu)
                return CostEstimate(predicted_median * r, sigma, tier, len(rows),
                                    text.format(n=len(rows)) + f": supplier prices run about {100 * r:.0f}% of the predicted winning price",
                                    ratio=r, quote_ids=[o.quote_id for o in rows if o.quote_id is not None])
        return CostEstimate(predicted_median * self.prior_fraction, SIGMA_FLOOR["prior"], "prior", 0,
                            f"assumption: {100 * self.prior_fraction:.0f}% of the predicted winning price (no quotes of yours to learn from yet)",
                            ratio=self.prior_fraction)

    # ------------------------------------------------------------------ evaluation
    def leave_one_out(self) -> list[dict]:
        """For every quote with a predicted price: estimate it from the tenant's OTHER quotes, and record the error.

        That is the evidence for how predictable this tenant's cost is before asking, by tier.
        """
        out = []
        for o in self.obs:
            if o.quote_id is None or not o.predicted_median:
                continue
            est = self.estimate(o.nsn, o.fsc, o.cages, o.predicted_median, o.quantity, exclude_quote_id=o.quote_id)
            if est is None:
                continue
            err = abs(est.median - o.price) / o.price
            out.append({"quote_id": o.quote_id, "nsn": o.nsn, "fsc": o.fsc, "tier": est.tier, "n": est.n,
                        "price": o.price, "estimate": est.median, "ape": err,
                        "inside_80": est.q10 <= o.price <= est.q90,
                        "prior_ape": abs(o.predicted_median * self.prior_fraction - o.price) / o.price})
        return out


def summarize_loo(rows: list[dict]) -> dict[str, dict]:
    """Median absolute % error and 80%-range coverage by tier (and overall), vs the flat prior."""
    out: dict[str, dict] = {}
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["tier"], []).append(r)
        by.setdefault("all", []).append(r)
    for tier, rs in by.items():
        ape = np.array([r["ape"] for r in rs])
        prior = np.array([r["prior_ape"] for r in rs])
        out[tier] = {"n": len(rs), "mdape": float(np.median(ape)), "within25": float(np.mean(ape <= 0.25)),
                     "coverage_80": float(np.mean([r["inside_80"] for r in rs])), "prior_mdape": float(np.median(prior))}
    return out
