"""Landed cost formula and per-org recommendation rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import OrgSettings, Recommendation, SolicitationLine
from app.services.landed_cost import compute_landed_cost
from tests.conftest import csrf, signup
from tests.helpers import build_world


def _settings(**kw):
    base = dict(packaging_cost_per_line=10.0, default_freight_per_line=10.0, inspection_allowance_per_line=0.0,
                origin_inspection_allowance_per_line=50.0, first_article_allowance_per_line=500.0,
                cost_of_capital_pct=0.10, payment_terms_days=30, default_overhead_pct=0.05, provisional_cost_fraction=0.7)
    base.update(kw)
    return SimpleNamespace(**base)


def _line(qty=100, insp="DESTINATION", fa=False):
    sol = SimpleNamespace(inspection_point=insp, first_article=fa)
    return SimpleNamespace(quantity=qty, solicitation=sol), sol


def test_landed_cost_formula_with_quote():
    line, sol = _line(qty=100, insp="ORIGIN", fa=True)
    quote = SimpleNamespace(id=7, unit_price=4.0, min_order_qty=None, freight_cost=25.0, freight_terms=None,
                            quote_request=SimpleNamespace(cage_code="1ABCD"))
    lc = compute_landed_cost(line, sol, _settings(), quote, predicted_median=6.0)
    financing = 4.0 * 0.10 * 30 / 365
    expected = 4.0 + 25 / 100 + 10 / 100 + 50 / 100 + 500 / 100 + financing + 0.05 * 4.0
    assert lc.landed == pytest.approx(expected)
    assert lc.financing_cost == pytest.approx(financing)
    assert not lc.provisional and lc.quote_id == 7


def test_moq_uplift_and_included_freight():
    line, sol = _line(qty=10)
    quote = SimpleNamespace(id=1, unit_price=2.0, min_order_qty=50, freight_cost=None, freight_terms="Prepaid",
                            quote_request=SimpleNamespace(cage_code="X"))
    lc = compute_landed_cost(line, sol, _settings(), quote, 5.0)
    assert lc.effective_unit_price == pytest.approx(10.0) and lc.freight_per_unit == 0 and lc.moq_note


def test_provisional_landed_cost_without_quote():
    line, sol = _line(qty=100)
    lc = compute_landed_cost(line, sol, _settings(), None, predicted_median=10.0)
    assert lc.provisional and lc.supplier_unit_price == pytest.approx(7.0)
    assert "provisional" in lc.basis


def test_org_recommendations_use_org_settings_and_set_aside(client, db):
    build_world(db, n_open=8)
    me = signup(client, "r@broker.example.com", org="Rec Org")
    org_id = me["organization"]["id"]
    recs = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id)).scalars().all()
    assert recs, "signup bootstraps recommendations for open lines"
    for r in recs:
        if r.recommended_bid is not None and not r.no_bid:
            assert r.recommended_bid >= r.landed_cost_per_unit * 1.10 - 0.01
    # Make the org ineligible for small-business set-asides and mark one line SB.
    line = db.get(SolicitationLine, recs[0].solicitation_line_id)
    line.solicitation.set_aside = "SDVOSB"
    db.commit()
    r = client.put("/api/settings", json={"set_aside_eligibility": ["SB"], "min_margin_pct": 0.2}, headers=csrf(client))
    assert r.status_code == 200
    db.expire_all()
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == line.id,
                                                  Recommendation.organization_id == org_id)).scalar_one()
    assert rec.no_bid and "SDVOSB" in rec.no_bid_reason
    assert db.get(OrgSettings, org_id).min_margin_pct == 0.2


def test_dashboard_lists_ranked_lines(client, db):
    build_world(db, n_open=8)
    signup(client, "d@broker.example.com", org="Dash Org")
    d = client.get("/api/dashboard").json()
    assert d["summary"]["lines"] == len(d["lines"]) > 0
    bids = [x for x in d["lines"] if not x["no_bid"]]
    assert [x["expected_profit"] for x in bids] == sorted([x["expected_profit"] for x in bids], reverse=True)
    sn = d["lines"][0]["solicitation_number"]
    detail = client.get(f"/api/rfq/{sn}").json()
    ln = detail["lines"][0]
    assert ln["prediction"]["levels"][0] == 0.05 and len(ln["history"]) > 0
    assert ln["recommendation"]["explanation"]["curve"]
