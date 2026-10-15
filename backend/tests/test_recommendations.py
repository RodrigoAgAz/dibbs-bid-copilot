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


def test_lines_without_history_or_quote_ask_for_a_quote_first(client, db):
    from datetime import date

    from app.models import PricePrediction, Solicitation
    from app.services.recommendations import refresh_stale_predictions

    build_world(db, n_open=6)
    me = signup(client, "q@broker.example.com", org="Quote First Org")
    line = db.execute(
        select(SolicitationLine).join(Solicitation).where(Solicitation.return_by_date >= date.today())
        .order_by(SolicitationLine.id)
    ).scalars().first()
    line.nsn, line.niin, line.approved_source_only = "5305999999998", "999999998", False
    db.query(PricePrediction).filter(PricePrediction.solicitation_line_id == line.id).delete()
    db.commit()
    refresh_stale_predictions(db)
    db.commit()
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == line.id,
                                                  Recommendation.organization_id == me["organization"]["id"])).scalar_one()
    assert not rec.no_bid and rec.recommended_bid is None and rec.expected_profit is None
    assert rec.explanation["needs_quote"] and rec.explanation["price_range"][0] < rec.explanation["price_range"][1]

    d = client.get("/api/dashboard").json()
    row = next(x for x in d["lines"] if x["line_id"] == line.id)
    assert row["flags"]["needs_quote"] and row["recommended_bid"] is None
    assert d["summary"]["needs_quote_lines"] >= 1
    bids = [x for x in d["lines"] if not x["no_bid"]]
    first_unpriced = next(i for i, x in enumerate(bids) if x["expected_profit"] is None)
    assert all(x["expected_profit"] is None for x in bids[first_unpriced:])  # quote-first lines come last

    # A supplier quote turns it into a priced recommendation.
    qr = client.post(f"/api/rfq/lines/{line.id}/quote-requests", json={"cage_code": "1ABC2"}, headers=csrf(client)).json()
    median = rec.explanation["distribution"]["median"]
    r = client.post(f"/api/quote-requests/{qr['id']}/quotes", json={"unit_price": round(median * 0.5, 4)}, headers=csrf(client))
    assert r.status_code == 201, r.text
    db.expire_all()
    rec = db.get(Recommendation, rec.id)
    assert not rec.provisional and not rec.explanation["needs_quote"]
    assert rec.no_bid or rec.recommended_bid is not None


def test_confidence_tiers():
    from app.services.recommendations import confidence_tier

    levels = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]

    def q(p10, p90, n, cold=False):
        prices = [p10 * 0.9, p10, *([p10 + (p90 - p10) * k / 8 for k in range(1, 8)]), p90, p90 * 1.1]
        return {"levels": levels, "prices": prices, "n_prior_awards": n, "cold_start": cold}

    assert confidence_tier(q(1.0, 1.8, 12)) == "high"
    assert confidence_tier(q(1.0, 3.0, 12)) == "medium"
    assert confidence_tier(q(1.0, 1.8, 2)) == "medium"
    assert confidence_tier(q(1.0, 20.0, 12)) == "low"
    assert confidence_tier(q(1.0, 1.5, 1)) == "low"
    assert confidence_tier(q(1.0, 1.5, 0, cold=True)) == "none"


def test_provisional_bids_target_the_likely_winning_price(client, db):
    build_world(db, n_open=8)
    me = signup(client, "t@broker.example.com", org="Target Org")
    recs = db.execute(select(Recommendation).where(Recommendation.organization_id == me["organization"]["id"])).scalars().all()
    priced = [r for r in recs if r.provisional and not r.no_bid and r.recommended_bid is not None]
    assert priced
    for r in priced:
        ex = r.explanation
        assert r.p_win_at_recommended >= 0.5 and ex["target_p_win"] == 0.5
        assert r.recommended_bid <= ex["distribution"]["median"] + 0.01
        assert ex["max_landed_cost"] == pytest.approx(r.recommended_bid / 1.10, abs=1e-3)
        assert ex["confidence"] in ("high", "medium", "low") and ex["rank_score"] is not None
    d = client.get("/api/dashboard").json()
    ranked = [x["rank_score"] for x in d["lines"] if not x["no_bid"] and x["rank_score"] is not None]
    assert ranked == sorted(ranked, reverse=True)


def test_lines_awarded_before_their_return_date_are_no_longer_opportunities(client, db):
    from datetime import date, timedelta

    from app.models import AwardHistory, Solicitation
    from app.services.recommendations import open_lines

    build_world(db, n_open=6)
    signup(client, "fast@broker.example.com", org="Fast Award Org")
    d = client.get("/api/dashboard").json()
    target = d["lines"][0]
    line = db.get(SolicitationLine, target["line_id"])
    sol = line.solicitation
    assert sol.return_by_date >= date.today()
    # A fast award posts before the return-by date.
    db.add(AwardHistory(nsn=line.nsn, award_date=date.today() - timedelta(days=1), unit_price=1.0, quantity=line.quantity,
                        contract_number="SPE-FAST-1", solicitation_number=sol.solicitation_number, source="award_listing"))
    db.commit()
    assert line.id not in {ln.id for ln in open_lines(db)}
    after = client.get("/api/dashboard").json()
    assert target["line_id"] not in {x["line_id"] for x in after["lines"]}
    assert after["summary"]["lines"] == d["summary"]["lines"] - 1
    # A cancelled solicitation is gone too.
    other = db.get(Solicitation, db.get(SolicitationLine, after["lines"][0]["line_id"]).solicitation_id)
    other.status = "Cancelled"
    db.commit()
    assert all(x["solicitation_number"] != other.solicitation_number for x in client.get("/api/dashboard").json()["lines"])
