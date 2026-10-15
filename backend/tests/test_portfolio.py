"""Portfolio limits, fulfilment losses in the bid, and the per-line sensitivity block."""

from __future__ import annotations

import math

import pytest
from sqlalchemy import select

from app.models import OrgSettings, Recommendation, SolicitationLine
from app.services.portfolio import portfolio_summary
from app.services.recommendations import refresh_org_recommendations
from dibbs_ml.bidding import recommend_bid
from dibbs_ml.cdf import PriceDistribution
from tests.conftest import csrf, signup
from tests.helpers import build_world


def test_fulfilment_losses_come_off_the_bid():
    d = PriceDistribution.lognormal(math.log(10), 0.2)
    clean = recommend_bid(d, 7.0, 100, min_margin_pct=0.10)
    lossy = recommend_bid(d, 7.0, 100, min_margin_pct=0.10, fulfilment_loss_pct=0.05)
    assert lossy.fulfilment_loss_pct == 0.05 and clean.fulfilment_loss_pct == 0.0
    # The minimum bid is net of losses: landed x 1.10 / 0.95.
    assert lossy.grid_low >= 7.0 * 1.10 / 0.95 - 1e-6 and clean.grid_low == pytest.approx(7.7)
    assert lossy.expected_profit < clean.expected_profit
    b = lossy.recommended_bid
    assert lossy.expected_profit == pytest.approx(lossy.p_win * (b * 0.95 - 7.0) * 100, rel=1e-6)


def test_sensitivity_and_portfolio_limits(client, db):
    build_world(db, n_open=10)
    me = signup(client, "port@broker.example.com", org="Portfolio Org")
    org_id = me["organization"]["id"]
    recs = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id)).scalars().all()
    priced = [r for r in recs if not r.no_bid and r.recommended_bid is not None]
    assert priced
    for r in priced:
        sens = r.explanation["sensitivity"]
        assert sens is not None and [s["name"] for s in sens["scenarios"]] == [
            "supplier price +10%", "competitors 5% lower", "competitors 5% higher"]
        assert isinstance(sens["fragile"], bool)
        lower = sens["scenarios"][1]
        if not lower["no_bid"] and lower["recommended_bid"] is not None:
            assert lower["recommended_bid"] <= r.recommended_bid + 1e-6  # cheaper competitors never raise the bid
        assert r.explanation["fulfilment_loss_pct"] == 0.02

    # No quotes yet: nothing is in flight, everything worth pursuing fits when hours are unlimited.
    s = db.get(OrgSettings, org_id)
    p = portfolio_summary(db, org_id, s)
    assert p.quoted_lines == 0 and p.capital_in_flight == 0 and p.p_no_award == 1.0
    assert p.pursue_candidates >= 1 and p.pursue_now and not p.deferred_capacity
    assert p.pursuit_cost_committed == pytest.approx(15.0 * len(p.pursue_now))

    # Quote three lines so they are priced against a cost, then apply tight limits.
    for r in priced[:3]:
        ln = db.get(SolicitationLine, r.solicitation_line_id)
        qr = client.post(f"/api/rfq/lines/{ln.id}/quote-requests", json={"cage_code": "1PORT"}, headers=csrf(client)).json()
        med = r.explanation["distribution"]["median"]
        assert client.post(f"/api/quote-requests/{qr['id']}/quotes", json={"unit_price": round(med * 0.6, 4), "lead_time_days": 10},
                           headers=csrf(client)).status_code == 201
    r = client.put("/api/settings", json={"weekly_sourcing_hours": 0.5, "pursuit_hours_per_line": 0.25,
                                          "working_capital_limit": 1.0, "max_open_awards": 1, "max_supplier_share": 0.4},
                   headers=csrf(client))
    assert r.status_code == 200 and r.json()["weekly_sourcing_hours"] == 0.5 and r.json()["working_capital_limit"] == 1.0
    db.expire_all()
    refresh_org_recommendations(db, org_id)
    db.commit()
    out = client.get("/api/portfolio").json()
    assert out["quoted_lines"] >= 1 and out["capital_in_flight"] > 0 and 0 <= out["p_no_award"] < 1
    assert out["capital_hold"] and out["working_capital_limit"] == 1.0  # $1 of capital holds everything back
    assert len(out["pursue_now"]) <= 2 and (out["deferred_capacity"] or out["pursue_candidates"] <= 2)
    # Every quoted line rests on the same supplier: concentration is flagged once there are three.
    if out["quoted_lines"] >= 3:
        assert out["concentrated_supplier"] == "1PORT" and out["supplier_shares"][0]["share"] == pytest.approx(1.0)
    assert any("capital" in n for n in out["notes"])
    # Limits can be cleared again.
    r = client.put("/api/settings", json={"working_capital_limit": None, "weekly_sourcing_hours": None}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["working_capital_limit"] is None
    out = client.get("/api/portfolio").json()
    assert out["capital_hold"] == [] and out["deferred_capacity"] == []
