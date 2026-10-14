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
    # Values extracted from the explanation JSON in SQL (the dashboard doesn't load whole explanations).
    assert all(isinstance(x["predicted_median"], float) and x["predicted_median"] > 0 for x in d["lines"])
    assert all(isinstance(x["flags"]["cold_start"], bool) for x in d["lines"])
    sn = d["lines"][0]["solicitation_number"]
    detail = client.get(f"/api/rfq/{sn}").json()
    ln = detail["lines"][0]
    assert ln["prediction"]["levels"][0] == 0.05 and len(ln["history"]) > 0
    assert ln["recommendation"]["explanation"]["curve"]


def test_recommendations_respect_the_min_win_probability(client, db):
    build_world(db, n_open=8)
    me = signup(client, "p@broker.example.com", org="Pwin Org")
    r = client.put("/api/settings", json={"min_p_win": 0.4}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["min_p_win"] == 0.4
    db.expire_all()
    recs = db.execute(select(Recommendation).where(Recommendation.organization_id == me["organization"]["id"])).scalars().all()
    priced = [x for x in recs if not x.no_bid and x.p_win_at_recommended is not None]
    assert priced and all(x.p_win_at_recommended >= 0.4 - 1e-6 for x in priced)
    assert client.put("/api/settings", json={"min_p_win": 0.01}, headers=csrf(client)).status_code == 422


def test_stale_predictions_are_repriced_when_history_arrives(client, db):
    from datetime import date, timedelta

    from app.models import AwardHistory, PricePrediction, Solicitation
    from app.services.pricing import latest_prediction
    from app.services.recommendations import refresh_stale_predictions

    build_world(db, n_open=6)
    signup(client, "s@broker.example.com", org="Stale Org")
    # An open line for a brand-new NSN that hasn't been priced yet.
    line = db.execute(
        select(SolicitationLine).join(Solicitation).where(Solicitation.return_by_date >= date.today())
        .order_by(SolicitationLine.id)
    ).scalars().first()
    line.nsn = "5305999999999"
    line.niin = line.nsn[4:]
    db.query(PricePrediction).filter(PricePrediction.solicitation_line_id == line.id).delete()
    db.commit()
    assert refresh_stale_predictions(db)["repredicted"] == 1  # unpriced lines count as stale
    db.commit()
    cold = latest_prediction(db, line.id)
    assert cold.quantiles["cold_start"] and cold.quantiles["history_rows"] == 0
    assert refresh_stale_predictions(db)["repredicted"] == 0  # nothing new: nothing repriced
    # Its procurement history shows up later (a PDF fetched by a later run, the backfill, ...).
    for i in range(4):
        db.add(AwardHistory(nsn=line.nsn, award_date=date.today() - timedelta(days=60 * (i + 1)), unit_price=0.05,
                            quantity=1000, contract_number=f"SPE7M0-STALE-{i}", source="rfq_pdf_history"))
    db.commit()
    out = refresh_stale_predictions(db)
    db.commit()
    assert out["repredicted"] == 1
    warm = latest_prediction(db, line.id)
    assert warm.id != cold.id and not warm.quantiles["cold_start"] and warm.quantiles["history_rows"] == 4
    assert db.query(PricePrediction).filter(PricePrediction.solicitation_line_id == line.id).count() >= 2
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == line.id)).scalar_one()
    assert rec.price_prediction_id == warm.id
