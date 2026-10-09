"""CDF construction and recommendation-engine math against analytic optima."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.optimize import minimize_scalar
from scipy.stats import lognorm, norm

from dibbs_ml.bidding import recommend_bid
from dibbs_ml.cdf import QUANTILE_LEVELS, PriceDistribution


def test_lognormal_quantiles_reproduce_lognormal_cdf():
    mu, sigma = math.log(12.0), 0.3
    d = PriceDistribution.lognormal(mu, sigma)
    xs = np.array([5.0, 8.0, 12.0, 15.0, 25.0, 40.0])
    expected = lognorm(s=sigma, scale=math.exp(mu)).cdf(xs)
    np.testing.assert_allclose(d.cdf(xs), expected, atol=1e-6)
    assert d.median == pytest.approx(12.0, rel=1e-6)
    for p in (0.01, 0.2, 0.5, 0.93, 0.99):
        assert d.cdf(d.ppf(p)) == pytest.approx(p, abs=1e-6)


def test_cdf_is_monotone_even_with_crossing_quantiles():
    # Crossing/tied raw quantiles get sorted and de-duplicated.
    prices = [10, 9, 11, 12, 12, 13, 12.5, 14, 15, 18, 17]
    d = PriceDistribution(QUANTILE_LEVELS, prices)
    xs = np.linspace(5, 30, 200)
    c = d.cdf(xs)
    assert np.all(np.diff(c) >= -1e-12)
    assert np.all((c >= 0) & (c <= 1))
    np.testing.assert_allclose(d.sf(xs) + c, 1.0)


@pytest.mark.parametrize(
    "mu,sigma,landed,qty",
    [(math.log(10), 0.25, 7.0, 100), (math.log(2.5), 0.4, 1.2, 1000), (math.log(150), 0.15, 120.0, 3)],
)
def test_argmax_matches_analytic_optimum(mu, sigma, landed, qty):
    d = PriceDistribution.lognormal(mu, sigma)

    def neg_profit(b):
        return -(1 - norm.cdf((math.log(b) - mu) / sigma)) * (b - landed) * qty

    hi = math.exp(mu + sigma * norm.ppf(0.95))
    opt = minimize_scalar(neg_profit, bounds=(landed, hi), method="bounded", options={"xatol": 1e-6})
    rec = recommend_bid(d, landed, qty, min_margin_pct=0.0, grid_points=2000)
    assert rec.recommended_bid == pytest.approx(opt.x, rel=0.003, abs=0.011)
    assert rec.expected_profit == pytest.approx(-opt.fun, rel=0.001)
    assert rec.p_win == pytest.approx(1 - norm.cdf((math.log(rec.recommended_bid) - mu) / sigma), abs=1e-6)


def test_conservative_and_aggressive_bids():
    d = PriceDistribution.lognormal(math.log(20), 0.3)
    rec = recommend_bid(d, 12.0, 50, min_margin_pct=0.1)
    assert rec.conservative_bid is not None and rec.conservative_p_win >= 0.7
    # Conservative is the highest-margin bid with P(win) >= 0.7: one cent more drops below 0.7.
    assert d.sf(rec.conservative_bid + 0.01) < 0.7 or rec.conservative_bid == rec.grid_high
    assert rec.aggressive_p_win == pytest.approx(0.3, abs=0.01)
    assert rec.aggressive_bid > rec.recommended_bid > rec.conservative_bid


def test_bids_are_cents_and_never_below_landed_cost_plus_margin():
    d = PriceDistribution.lognormal(math.log(3.0), 0.35)
    landed = 2.333
    rec = recommend_bid(d, landed, 10, min_margin_pct=0.07)
    for b in (rec.recommended_bid, rec.conservative_bid, rec.aggressive_bid):
        if b is not None:
            assert round(b, 2) == b
            assert b >= landed * 1.07 - 1e-9


def test_no_bid_rules():
    d = PriceDistribution.lognormal(math.log(10), 0.2)
    # Landed cost above the 95th percentile.
    r = recommend_bid(d, 30.0, 10)
    assert r.no_bid and "cost too high" in r.no_bid_reason
    # Expected profit floor.
    r = recommend_bid(d, 7.0, 1, min_expected_profit=50)
    assert r.no_bid and "below" in r.no_bid_reason
    # Approved-source-only without a conforming quote.
    r = recommend_bid(d, 7.0, 100, approved_source_only=True, has_conforming_quote=False)
    assert r.no_bid and "approved-source-only" in r.no_bid_reason
    # Due date passed.
    r = recommend_bid(d, 7.0, 100, return_by_passed=True)
    assert r.no_bid and r.no_bid_reasons[0] == "return-by date has passed"
    # Line value cap.
    r = recommend_bid(d, 7.0, 100_000, max_line_value=25_000)
    assert r.no_bid and "max line value" in r.no_bid_reason
    # A good line is a bid.
    r = recommend_bid(d, 7.0, 100, min_expected_profit=25)
    assert not r.no_bid and r.no_bid_reason is None
