"""Tenant cost model in the recommendation flow, the triage score and its components."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    ApprovedSource,
    AwardHistory,
    Company,
    OrgSettings,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    SolicitationLine,
    SupplierContact,
)
from app.services.contestability import contestability
from app.services.cost_model import supplier_response_stats, tenant_quote_observations
from app.services.recommendations import refresh_line_for_org
from tests.conftest import csrf, signup
from tests.helpers import build_world
from tests.test_sourcing import _line_with_sources


def _provisional(db, org_id):
    return [r for r in db.execute(select(Recommendation).where(Recommendation.organization_id == org_id)).scalars()
            if r.provisional and not r.no_bid and r.recommended_bid is not None]


def test_unquoted_lines_get_a_triage_score_from_the_prior_and_quotes_teach_the_cost_model(client, db):
    build_world(db, n_open=12)
    me = signup(client, "triage@broker.example.com", org="Triage Org")
    org_id = me["organization"]["id"]
    recs = _provisional(db, org_id)
    assert recs
    for r in recs:
        ex = r.explanation
        assert ex["cost_estimate"]["tier"] == "prior" and "assumption" in ex["cost_estimate"]["basis"]
        assert r.landed_breakdown["supplier_unit_price"] == pytest.approx(ex["distribution"]["median"] * 0.7, rel=1e-3)
        t = ex["triage"]
        assert t is not None and ex["triage_score"] == pytest.approx(t["score"], abs=0.01)
        assert 0 <= t["p_sourceable"] <= 1 and 0 <= t["p_quote_in_time"] <= 1 and 0 < t["p_winnable"] <= 1
        assert t["pursuit_cost"] == 15.0
        assert t["score"] == pytest.approx(
            t["expected_profit_if_sourced"] * t["p_sourceable"] * t["p_quote_in_time"] * t["p_winnable"] - 15.0, rel=2e-3, abs=0.05)
        assert ex["contestability"]["p_winnable"] == pytest.approx(t["p_winnable"], abs=1e-3)
        assert ex["competitors"] is not None and "reseller_share_fsc" in ex["competitors"]
    d = client.get("/api/dashboard").json()
    rows = [x for x in d["lines"] if x["flags"]["provisional"] and not x["no_bid"] and x["recommended_bid"] is not None]
    assert rows and all(x["triage_score"] is not None for x in rows)
    # The pursuit cost is an org setting.
    r = client.put("/api/settings", json={"pursuit_cost_per_line": 40}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["pursuit_cost_per_line"] == 40

    # Quotes for other parts in the same FSC teach the cost model: the provisional cost moves off the assumption.
    target = recs[0]
    line = db.get(SolicitationLine, target.solicitation_line_id)
    others = recs[1:4]
    assert len(others) == 3
    for r in others:  # three other parts in the target's supply class: enough for the FSC tier
        db.get(SolicitationLine, r.solicitation_line_id).fsc = line.fsc
    db.commit()
    for i, r in enumerate(others):
        ln = db.get(SolicitationLine, r.solicitation_line_id)
        qr = client.post(f"/api/rfq/lines/{ln.id}/quote-requests", json={"cage_code": f"1COS{i}"}, headers=csrf(client)).json()
        med = r.explanation["distribution"]["median"]
        assert client.post(f"/api/quote-requests/{qr['id']}/quotes", json={"unit_price": round(med * 0.5, 4)},
                           headers=csrf(client)).status_code == 201
    obs = tenant_quote_observations(db, org_id)
    assert len(obs) == len(others) and all(o.predicted_median for o in obs)
    db.expire_all()
    rec = refresh_line_for_org(db, org_id, db.get(SolicitationLine, line.id))
    db.commit()
    est = rec.explanation["cost_estimate"]
    assert est["tier"] == "fsc" and est["n"] == len(others) and est["ratio"] == pytest.approx(0.5, rel=0.05)
    assert rec.landed_breakdown["supplier_unit_price"] == pytest.approx(rec.explanation["distribution"]["median"] * 0.5, rel=0.05)
    assert "quotes" in rec.landed_breakdown["basis"]
    assert rec.explanation["triage"]["pursuit_cost"] == 40.0


def test_response_history_drives_p_quote_in_time(client, db):
    build_world(db, n_open=6)
    me = signup(client, "resp@broker.example.com", org="Response Org")
    org_id = me["organization"]["id"]
    st = supplier_response_stats(db, org_id)
    assert st.total_sent == 0 and st.rate("1ABCD", True) == pytest.approx(0.30) and st.rate("1ABCD", False) == 0.02
    # Ten requests to one supplier, nine answered within a day.
    line = db.get(SolicitationLine, _provisional(db, org_id)[0].solicitation_line_id)
    db.add(SupplierContact(organization_id=org_id, cage_code="1FAST", email="s@fast.example.com", fsc_tags=[line.fsc]))
    now = datetime.now(UTC)
    lines = db.execute(select(SolicitationLine).order_by(SolicitationLine.id)).scalars().all()[:10]
    assert len(lines) == 10
    for i, ln in enumerate(lines):  # one request per line and supplier (unique constraint)
        db.add(QuoteRequest(organization_id=org_id, solicitation_line_id=ln.id, cage_code="1FAST", reply_token=f"tok{i}",
                            status=QuoteRequestStatus.parsed if i < 9 else QuoteRequestStatus.sent,
                            sent_at=now - timedelta(days=3), replied_at=(now - timedelta(days=2, hours=20)) if i < 9 else None,
                            email_to="s@fast.example.com"))
    db.commit()
    st = supplier_response_stats(db, org_id)
    assert st.sent["1FAST"] == 10 and st.answered["1FAST"] == 9
    assert st.rate("1FAST", True) > 0.8 and st.median_days("1FAST") == pytest.approx(4 / 24, abs=0.01)
    assert st.rate("1OTHR", True) == pytest.approx(st.overall_rate)


def test_contestability_is_the_shrunk_reseller_share(client, db):
    from app.services.competitors import clear_competitor_index
    from dibbs_ml.competitors import SHRINK_K

    build_world(db, n_open=6)
    me = signup(client, "cont@broker.example.com", org="Contest Org")
    org_id = me["organization"]["id"]
    line = db.execute(select(SolicitationLine)).scalars().first()
    line.nsn, line.niin, line.fsc = "5399000000777", "000000777", "5399"  # a supply class with no other awards
    db.commit()
    clear_competitor_index()
    c = contestability(db, line, org_id)
    assert c.n_awards == 0 and c.p_winnable == pytest.approx(0.7) and "no award history" in c.reason
    for i in range(5):  # the manufacturer wins 4 of 5
        db.add(AwardHistory(nsn=line.nsn, award_date=datetime(2025, 1 + i, 1).date(), unit_price=1.0, quantity=10,
                            contract_number=f"C{i}", awardee_cage="1OEM1" if i < 4 else "2RES2", source="rfq_pdf_history"))
    db.add(ApprovedSource(nsn=line.nsn, cage_code="1OEM1", part_number="P-1"))
    db.commit()
    clear_competitor_index()
    c = contestability(db, line, org_id)
    fsc_share = 1 / 5  # the only awards in this supply class are these five
    assert c.incumbent_cage == "1OEM1" and c.incumbent_is_manufacturer and c.incumbent_share == pytest.approx(0.8)
    assert c.p_winnable == pytest.approx((1 + SHRINK_K * fsc_share) / (5 + SHRINK_K))
    assert "resellers won 1 of 5" in c.reason and c.competitors is not None
    assert [p.cage for p in c.competitors.incumbents] == ["1OEM1", "2RES2"]
    # The broker's own CAGE among the winners counts as a reseller.
    from app.models import Organization

    db.get(Organization, org_id).cage_code = "1OEM1"
    db.commit()
    mine = contestability(db, line, org_id)
    assert mine.reseller_share_nsn == 1.0 and mine.p_winnable > c.p_winnable
    assert db.get(Company, "1OEM1") is None  # the approved-source rule did not need a company row


def test_supplier_price_ceiling_inverts_the_landed_cost(client, db):
    from types import SimpleNamespace

    from app.services.landed_cost import compute_landed_cost
    from app.services.triage import supplier_price_ceiling

    s = SimpleNamespace(packaging_cost_per_line=10.0, default_freight_per_line=10.0, inspection_allowance_per_line=0.0,
                        origin_inspection_allowance_per_line=50.0, first_article_allowance_per_line=500.0,
                        cost_of_capital_pct=0.10, payment_terms_days=30, default_overhead_pct=0.05, provisional_cost_fraction=0.7)
    sol = SimpleNamespace(inspection_point="ORIGIN", first_article=True)
    line = SimpleNamespace(quantity=100, unit_of_issue="EA", solicitation=sol)
    lc = compute_landed_cost(line, sol, s, None, predicted_median=10.0)
    ceiling = supplier_price_ceiling(lc.landed, lc, s)
    assert ceiling == pytest.approx(lc.supplier_unit_price, rel=1e-6)


def test_settings_row_has_the_new_default(client, db):
    me = signup(client, "def@broker.example.com", org="Default Org")
    assert db.get(OrgSettings, me["organization"]["id"]).pursuit_cost_per_line == 15.0
    assert _line_with_sources is not None  # imported helper stays available for the sourcing tests


def test_evaluation_rules_cap_automated_bids_and_move_fast_award_deadlines(client, db):
    from datetime import date

    from app.services.competitors import clear_competitor_index
    from app.services.evaluation import add_business_days, evaluate_line, evaluation_mode

    build_world(db, n_open=6)
    me = signup(client, "eval@broker.example.com", org="Eval Org")
    org_id = me["organization"]["id"]
    rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                  Recommendation.recommended_bid.is_not(None))).scalars().first()
    ex = rec.explanation["evaluation"]
    assert ex["mode"] in ("automated", "fast_automated", "aidc", "manual") and "handling_days" in ex
    assert isinstance(ex["factors"], list) and ex["return_by"] is not None
    line = db.get(SolicitationLine, rec.solicitation_line_id)
    sol = line.solicitation
    # Mode rules.
    sol.type_code, sol.first_article, sol.fast_award, sol.aidc = "T", False, False, False
    assert evaluation_mode(sol) == ("automated", [])
    sol.fast_award = True
    assert evaluation_mode(sol)[0] == "fast_automated"
    sol.first_article = True
    mode, why = evaluation_mode(sol)
    assert mode == "manual" and "first article" in why[0]
    sol.first_article, sol.fast_award, sol.type_code = False, False, "Q"
    assert evaluation_mode(sol)[0] == "manual"
    sol.type_code = "T"
    # Fast auto evaluation: the sourcing deadline moves to 3 business days after issue.
    sol.fast_award, sol.issue_date, sol.return_by_date = True, date(2026, 10, 12), date(2026, 10, 30)
    s = db.get(OrgSettings, org_id)
    ev = evaluate_line(line, sol, s, 10.0, None, None, False, today=date(2026, 10, 13))
    assert ev.effective_deadline == add_business_days(date(2026, 10, 12), 3) == date(2026, 10, 15)
    assert ev.effective_days_left == 2 and any("fast auto evaluation" in n for n in ev.notes)
    # A quote whose lead time plus handling does not fit the required delivery.
    from types import SimpleNamespace

    line.delivery_days = 30
    quote = SimpleNamespace(lead_time_days=28, conforms_to_part_number=True, freight_terms="FOB origin")
    sol.fob = "DESTINATION"
    ev = evaluate_line(line, sol, s, 10.0, None, quote, True, today=date(2026, 10, 13))
    assert ev.delivery_feasible is False and any("handling" in x for x in ev.exceptions)
    assert any("FOB origin" in x for x in ev.exceptions)
    # The automated price band caps the bid grid when the class's history is tight (a supply class of its own, so the
    # synthetic world's other awards do not set the band).
    from app.models import PricePrediction

    line.nsn, line.niin, line.fsc = "5399000000778", "000000778", "5399"
    db.query(PricePrediction).filter(PricePrediction.solicitation_line_id == line.id).delete()
    for i in range(40):
        db.add(AwardHistory(nsn=line.nsn, award_date=date(2024, 1, 1) + timedelta(days=20 * i), unit_price=10.0 * 1.01 ** i,
                            quantity=10, contract_number=f"B{i}", awardee_cage="2RES2", source="rfq_pdf_history",
                            solicitation_number=f"SPE4A6{24 + i // 20}T{i:04d}"))
    db.commit()
    clear_competitor_index()
    sol.fast_award = False
    db.commit()
    rec = refresh_line_for_org(db, org_id, line)
    db.commit()
    ev = rec.explanation["evaluation"]
    assert ev["cap_bid"] is not None and ev["cap_n"] >= 39 and ev["cap_ratio"] < 1.1
    if rec.recommended_bid is not None:
        assert rec.recommended_bid <= ev["cap_bid"] + 1e-6
    else:
        assert "automated price band" in (rec.no_bid_reason or "")
    # Handling days are an org setting used by the delivery check.
    r = client.put("/api/settings", json={"handling_days": 7}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["handling_days"] == 7
