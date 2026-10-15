"""Catalog prices as a cost tier: after the tenant's own quotes for the NSN, before any ratio tier."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dibbs_ml.cost_model import SIGMA_FLOOR, QuoteObs, TenantCostModel

TODAY = date(2026, 10, 15)


def obs(nsn="5305000000001", fsc="5305", price=1.0, qty=100, days_ago=10, pred=2.0, qid=None):
    return QuoteObs(nsn=nsn, fsc=fsc, cages=frozenset(), price=price, quantity=qty,
                    when=TODAY - timedelta(days=days_ago), predicted_median=pred, quote_id=qid)


def test_catalog_tier_sits_between_nsn_quotes_and_ratio_tiers():
    rows = [obs(nsn=f"53050000000{i:02d}", price=0.6 * 2.0, pred=2.0, qid=i) for i in range(5)]
    m = TenantCostModel(rows, prior_fraction=0.7, as_of=TODAY)
    # No quotes for this NSN, but an imported list price for its approved part: the catalog tier wins over the FSC ratio.
    e = m.estimate("5305999999999", "5305", None, predicted_median=10.0, catalog_prices=[7.5])
    assert e.tier == "catalog" and e.n == 1 and e.median == pytest.approx(7.5) and e.sigma == SIGMA_FLOOR["catalog"]
    assert e.ratio == pytest.approx(0.75) and "catalog" in e.basis
    # Two catalog rows: log-space median, spread at least the floor.
    e = m.estimate("5305999999999", "5305", None, predicted_median=10.0, catalog_prices=[6.0, 9.0])
    assert e.tier == "catalog" and e.n == 2 and e.median == pytest.approx((6.0 * 9.0) ** 0.5)
    # The tenant's own quote for the NSN still comes first.
    m2 = TenantCostModel(rows + [obs(nsn="5305999999999", price=5.0, qid=99)], as_of=TODAY)
    assert m2.estimate("5305999999999", "5305", None, 10.0, quantity=100, catalog_prices=[7.5]).tier == "nsn"
    # Without a predicted price a catalog price still gives an estimate; nothing else does.
    assert m.estimate("5305999999999", "5305", None, None, catalog_prices=[7.5]).tier == "catalog"
    assert m.estimate("5305999999999", "5305", None, None) is None
    # Non-positive catalog values are ignored.
    assert m.estimate("5305999999999", "5305", None, 10.0, catalog_prices=[0.0, -1.0]).tier == "fsc"
