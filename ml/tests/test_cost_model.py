"""Tenant cost model: tiers, fallbacks, quantity adjustment, leave-one-out evaluation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dibbs_ml.cost_model import MIN_OBS, SIGMA_FLOOR, CostEstimate, QuoteObs, TenantCostModel, summarize_loo

TODAY = date(2026, 10, 15)


def obs(nsn="5305000000001", fsc="5305", cages=(), price=1.0, qty=100, days_ago=10, pred=2.0, qid=None):
    return QuoteObs(nsn=nsn, fsc=fsc, cages=frozenset(cages), price=price, quantity=qty,
                    when=TODAY - timedelta(days=days_ago), predicted_median=pred, quote_id=qid)


def test_prior_when_the_tenant_has_no_quotes():
    m = TenantCostModel([], prior_fraction=0.7, as_of=TODAY)
    e = m.estimate("5305000000001", "5305", None, predicted_median=10.0)
    assert e.tier == "prior" and e.median == pytest.approx(7.0) and e.n == 0 and "assumption" in e.basis
    assert e.sigma == SIGMA_FLOOR["prior"] and e.q10 < 7.0 < e.q90
    assert m.estimate("5305000000001", "5305", None, predicted_median=None) is None


def test_nsn_tier_wins_and_adjusts_for_quantity():
    m = TenantCostModel([obs(price=1.0, qty=100, qid=1), obs(price=1.2, qty=100, qid=2)], as_of=TODAY)
    e = m.estimate("5305000000001", "5305", None, predicted_median=5.0, quantity=100)
    assert e.tier == "nsn" and e.n == 2 and e.quote_ids == [1, 2]
    assert e.median == pytest.approx((1.0 * 1.2) ** 0.5, rel=1e-6)  # log-space median of two
    assert e.sigma >= SIGMA_FLOOR["nsn"]
    big = m.estimate("5305000000001", "5305", None, predicted_median=5.0, quantity=10_000)
    assert big.median < e.median  # bigger buys price lower (elasticity -0.14)
    assert e.ratio == pytest.approx(e.median / 5.0)


def test_cage_then_fsc_then_org_ratio_tiers():
    rows = [obs(nsn=f"53050000000{i:02d}", cages=("1ABC2",), price=0.6 * 2.0, pred=2.0, qid=i) for i in range(2)]
    rows += [obs(nsn=f"53050000001{i:02d}", price=0.8 * 4.0, pred=4.0, qid=10 + i) for i in range(3)]
    rows += [obs(nsn=f"53100000000{i:02d}", fsc="5310", price=0.9 * 1.0, pred=1.0, qid=20 + i) for i in range(3)]
    m = TenantCostModel(rows, prior_fraction=0.7, as_of=TODAY)
    # Same manufacturer: 2 quotes at 60% of the predicted price.
    e = m.estimate("5305999999999", "5305", {"1ABC2"}, predicted_median=10.0)
    assert e.tier == "cage" and e.n == 2 and e.median == pytest.approx(6.0) and e.ratio == pytest.approx(0.6)
    # No manufacturer match: the FSC's 5 quotes (60% x2 and 80% x3 -> median ratio 0.8).
    e = m.estimate("5305999999999", "5305", {"ZZZZZ"}, predicted_median=10.0)
    assert e.tier == "fsc" and e.n == 5 and e.median == pytest.approx(8.0)
    # Another FSC with only 3 quotes: enough for the FSC tier.
    e = m.estimate("5310999999999", "5310", None, predicted_median=10.0)
    assert e.tier == "fsc" and e.median == pytest.approx(9.0)
    # An FSC the tenant never quoted: the org-wide ratio (8 quotes).
    e = m.estimate("4730999999999", "4730", None, predicted_median=10.0)
    assert e.tier == "org" and e.n == 8 and 6.0 < e.median < 9.0
    assert "across all supply classes" in e.basis


def test_minimum_observations_per_tier():
    rows = [obs(nsn=f"53050000000{i:02d}", price=1.0, pred=2.0, qid=i) for i in range(MIN_OBS["fsc"] - 1)]
    m = TenantCostModel(rows, as_of=TODAY)
    e = m.estimate("5305999999999", "5305", None, predicted_median=10.0)
    assert e.tier == "prior"  # 2 quotes in the FSC are not enough for a ratio


def test_recent_quotes_are_preferred_but_old_ones_still_count():
    old = [obs(price=1.0, days_ago=900, qid=1)]
    m = TenantCostModel(old, as_of=TODAY)
    assert m.estimate("5305000000001", "5305", None, 2.0, quantity=100).tier == "nsn"  # nothing newer: the old quote is used
    m = TenantCostModel(old + [obs(price=3.0, days_ago=5, qid=2)], as_of=TODAY)
    assert m.estimate("5305000000001", "5305", None, 2.0, quantity=100).median == pytest.approx(3.0)  # the old one is dropped


def test_p_at_or_below_and_dict():
    e = CostEstimate(median=10.0, sigma=0.3, tier="fsc", n=5, basis="x", ratio=0.7)
    assert e.p_at_or_below(10.0) == pytest.approx(0.5)
    assert e.p_at_or_below(e.q90) == pytest.approx(0.9, abs=1e-6)
    assert e.p_at_or_below(0.0) == 0.0
    d = e.to_dict()
    assert d["tier"] == "fsc" and d["q10"] < d["median"] < d["q90"]


def test_leave_one_out_scores_each_quote_against_the_others():
    rows = [obs(nsn=f"53050000000{i:02d}", price=0.7 * 2.0 * (1 + 0.05 * i), pred=2.0, qid=i) for i in range(6)]
    rows.append(obs(nsn="5305000000000", price=1.5, pred=2.0, qid=99))  # a second quote for NSN ...00
    m = TenantCostModel(rows, prior_fraction=0.7, as_of=TODAY)
    loo = m.leave_one_out()
    assert len(loo) == len(rows)
    by_id = {r["quote_id"]: r for r in loo}
    assert by_id[99]["tier"] == "nsn"  # judged from the other quote for its NSN
    assert all(r["tier"] in ("nsn", "fsc", "org") for r in loo)
    s = summarize_loo(loo)
    assert s["all"]["n"] == len(rows) and 0 <= s["all"]["mdape"] < 1 and 0 <= s["all"]["coverage_80"] <= 1
    assert "prior_mdape" in s["all"]
