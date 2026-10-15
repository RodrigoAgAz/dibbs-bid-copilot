"""Evidence behind every number: pricing-unit normalization, quote evidence states, no rank from an assumed cost,
decision snapshots, and history judged on what was shown before the award."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    AwardHistory,
    Organization,
    Recommendation,
    RecommendationSnapshot,
    SolicitationLine,
    SupplierQuote,
)
from app.services.landed_cost import compute_landed_cost, price_per_issue_unit
from app.services.llm import ExtractionContext, MockLLM
from app.services.sourcing import history_median
from tests.conftest import csrf, signup
from tests.helpers import build_world
from tests.test_recommendations import _line, _settings
from tests.test_sourcing import _draft, _line_with_sources, _setup

CTX = ExtractionContext(nsn="5305011209897", part_numbers=["MS24693-C274"], quantity=100, unit_of_issue="EA",
                        today=date(2026, 10, 9))


# ----------------------------------------------------------------------------- extraction
@pytest.mark.parametrize(
    "text,uoi,unit,basis",
    [
        ("Unit price $12.50 per 100, lead time 10 days.", "EA", "HD", 100.0),
        ("Unit price $12.50 per 100, lead time 10 days.", "HD", "HD", 1.0),
        ("We can do $0.488/ea, 10 business days", "EA", "EA", 1.0),
        ("Price $4.00 per M, in stock.", "EA", "M", 1000.0),
        ("$3.00 for 25 pcs, ships in 2 days", "EA", None, 25.0),
        ("$30.00 per box, 5 days", "EA", "BX", None),
        ("Unit price: $12.34, lead time 4 weeks ARO.", "EA", None, None),
    ],
)
def test_mock_reads_the_pricing_basis(text, uoi, unit, basis):
    q = MockLLM().extract_quote(text, ExtractionContext(nsn=CTX.nsn, part_numbers=[], quantity=100, unit_of_issue=uoi,
                                                        today=CTX.today))
    assert q.is_quote and q.price_unit == unit and q.price_basis_qty == basis


def test_mock_never_fills_in_the_requested_part_number():
    q = MockLLM().extract_quote("Yes, we can do $4.20 each, 2 weeks ARO.", CTX)
    assert q.is_quote and q.quoted_part_number is None


# ----------------------------------------------------------------------------- normalization
def test_supplier_price_is_normalized_to_the_unit_of_issue():
    q = SimpleNamespace(unit_price=12.5, price_unit="HD", price_basis_qty=100.0)
    assert price_per_issue_unit(q, "EA") == (0.125, None, "confirmed")
    q = SimpleNamespace(unit_price=12.5, price_unit="HD", price_basis_qty=None)
    assert price_per_issue_unit(q, "EA") == (0.125, None, "confirmed")  # convertible count units
    assert price_per_issue_unit(q, "HD") == (12.5, None, "confirmed")
    q = SimpleNamespace(unit_price=2.0, price_unit=None, price_basis_qty=None)
    assert price_per_issue_unit(q, "EA") == (2.0, None, "inferred")  # assumed per unit of issue
    q = SimpleNamespace(unit_price=30.0, price_unit="BX", price_basis_qty=None)
    price, problem, _ = price_per_issue_unit(q, "EA")
    assert price is None and "cannot convert" in problem
    line, sol = _line(qty=100)
    quote = SimpleNamespace(id=3, unit_price=12.5, price_unit="HD", price_basis_qty=100.0, min_order_qty=None,
                            freight_cost=None, freight_terms="prepaid", quote_request=SimpleNamespace(cage_code="X"))
    lc = compute_landed_cost(line, sol, _settings(), quote, 5.0)
    assert lc.supplier_unit_price == pytest.approx(0.125) and "per 100" in lc.basis and not lc.provisional


# ----------------------------------------------------------------------------- sourcing evidence
def test_aso_quote_without_a_stated_part_number_is_not_conforming(client, db):
    _, line_ids, org_id = _setup(client, db)
    try:
        ln = _line_with_sources(db, line_ids, aso=True)
    except AssertionError:
        ln = _line_with_sources(db, line_ids)
        ln.approved_source_only = True
        db.commit()
    qr = _draft(client, db, ln)
    assert qr.part_number  # we asked for an approved part number...
    r = client.post(f"/api/quote-requests/{qr.id}/simulate-reply", headers=csrf(client),
                    json={"text": "Yes, we can do $4.20 each, lead time 2 weeks ARO."})
    assert r.status_code == 200
    q = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == qr.id)).scalar_one()
    # ...but the supplier never confirmed it, so the quote is not conforming and says why.
    assert not q.conforms_to_part_number and q.needs_review
    assert "did not state the part number" in q.review_reason
    assert q.evidence["part_number"] == "unknown" and q.evidence["unit"] == "confirmed"


def test_per_hundred_quote_is_normalized_and_unconvertible_units_wait_for_review(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids, aso=False)
    ln.unit_of_issue = "EA"
    db.commit()
    med = history_median(db, ln.nsn)
    qr = _draft(client, db, ln)
    pn = qr.part_number or "ABC-1"
    r = client.post(f"/api/quote-requests/{qr.id}/simulate-reply", headers=csrf(client),
                    json={"text": f"Unit price ${med * 100:.2f} per 100, lead time 10 days, P/N {pn}."})
    assert r.status_code == 200
    q = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == qr.id)).scalar_one()
    assert q.price_unit == "HD" and q.price_basis_qty == 100 and q.evidence["unit"] == "confirmed"
    assert price_per_issue_unit(q, "EA")[0] == pytest.approx(med, rel=1e-3)
    if not q.needs_review:
        rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == ln.id)).scalar_one()
        assert rec.landed_breakdown["supplier_unit_price"] == pytest.approx(med, rel=1e-3)

    # A price per box with no pack size cannot be a cost: it waits for review and cannot be approved as is.
    other = client.post(f"/api/rfq/lines/{ln.id}/quote-requests", json={"cage_code": "1BOXY"}, headers=csrf(client)).json()
    r = client.post(f"/api/quote-requests/{other['id']}/simulate-reply", headers=csrf(client),
                    json={"text": "$30.00 per box, ships in 5 days."})
    assert r.status_code == 200
    q2 = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == other["id"])).scalar_one()
    assert q2.needs_review and "pricing unit" in q2.review_reason and q2.price_unit == "BX"
    r = client.patch(f"/api/supplier-quotes/{q2.id}", json={"mark_reviewed": True}, headers=csrf(client))
    assert r.status_code == 422 and "price basis" in r.json()["detail"].lower()
    r = client.patch(f"/api/supplier-quotes/{q2.id}", json={"price_basis_qty": 50, "mark_reviewed": True}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["evidence"]["unit"] == "confirmed" and r.json()["needs_review"] is False
    db.expire_all()
    assert price_per_issue_unit(db.get(SupplierQuote, q2.id), "EA")[0] == pytest.approx(0.6)


def test_manual_quote_marks_the_requested_part_number_as_inferred(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids)
    qr = _draft(client, db, ln)
    r = client.post(f"/api/quote-requests/{qr.id}/quotes", json={"unit_price": 2.5}, headers=csrf(client))
    assert r.status_code == 201
    ev = r.json()["evidence"]
    assert ev["part_number"] == ("inferred" if qr.part_number else "unknown") and ev["lead_time"] == "unknown"
    r = client.post(f"/api/quote-requests/{qr.id}/quotes", json={"unit_price": 2.5, "price_unit": "BX"}, headers=csrf(client))
    assert r.status_code == 422


# ----------------------------------------------------------------------------- ranking and snapshots
def test_assumed_cost_gives_no_rank_score_and_decisions_are_snapshotted(client, db):
    build_world(db, n_open=8)
    me = signup(client, "snap@broker.example.com", org="Snapshot Org")
    org_id = me["organization"]["id"]
    recs = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id)).scalars().all()
    provisional = [r for r in recs if r.provisional and not r.no_bid and r.recommended_bid is not None]
    assert provisional
    for r in provisional:
        ex = r.explanation
        assert ex["rank_score"] is None and ex["provisional_rank"] > 0 and ex["expected_profit_basis"] == "assumed_cost"
    d = client.get("/api/dashboard").json()
    assert all(x["rank_score"] is None for x in d["lines"] if x["flags"]["provisional"])
    # One snapshot per recommendation so far; recomputing with the same inputs adds none.
    n_snaps = db.query(RecommendationSnapshot).filter(RecommendationSnapshot.organization_id == org_id).count()
    assert n_snaps == len(recs)
    line = db.get(SolicitationLine, provisional[0].solicitation_line_id)
    assert client.post(f"/api/rfq/lines/{line.id}/refresh", headers=csrf(client)).status_code == 200
    assert db.query(RecommendationSnapshot).filter(RecommendationSnapshot.organization_id == org_id).count() == n_snaps
    # A quote changes the decision: a new snapshot, and the profit basis becomes the quote.
    qr = client.post(f"/api/rfq/lines/{line.id}/quote-requests", json={"cage_code": "1SNAP"}, headers=csrf(client)).json()
    median = provisional[0].explanation["distribution"]["median"]
    r = client.post(f"/api/quote-requests/{qr['id']}/quotes", json={"unit_price": round(median * 0.6, 4)}, headers=csrf(client))
    assert r.status_code == 201 and r.json()["evidence"]["unit"] == "inferred"
    db.expire_all()
    rec = db.get(Recommendation, provisional[0].id)
    assert not rec.provisional and rec.explanation["expected_profit_basis"] == "quote"
    assert (rec.explanation["rank_score"] is not None) == (not rec.no_bid)
    assert rec.explanation["quote_evidence"]["unit"] == "inferred"
    snaps = client.get(f"/api/rfq/lines/{line.id}/snapshots").json()
    assert [s["kind"] for s in snaps] == ["recommendation", "recommendation"]
    assert snaps[0]["payload"]["provisional"] is True and snaps[1]["payload"]["provisional"] is False
    assert snaps[1]["payload"]["quotes_used"] == [r.json()["id"]]
    # Broker actions are snapshotted too.
    assert client.post(f"/api/rfq/lines/{line.id}/actual-bid", json={"actual_bid": 9.99}, headers=csrf(client)).status_code == 200
    r = client.post(f"/api/rfq/lines/{line.id}/actual-cost", json={"actual_cost_per_unit": 5.5}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["actual_cost_per_unit"] == 5.5
    kinds = [s["kind"] for s in client.get(f"/api/rfq/lines/{line.id}/snapshots").json()]
    assert kinds[-2:] == ["actual_bid", "actual_cost"]


def test_history_judges_the_snapshot_before_the_award_and_separates_margins(client, db):
    build_world(db, n_open=6)
    me = signup(client, "hist@broker.example.com", org="History Org")
    org_id = me["organization"]["id"]
    db.get(Organization, org_id).cage_code = "9HIST"
    db.commit()
    rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                  Recommendation.no_bid.is_(False),
                                                  Recommendation.recommended_bid.is_not(None))).scalars().first()
    line = db.get(SolicitationLine, rec.solicitation_line_id)
    snapshot_bid = rec.recommended_bid
    assert client.post(f"/api/rfq/lines/{line.id}/actual-bid", json={"actual_bid": 10.0}, headers=csrf(client)).status_code == 200
    # Everything so far happened "yesterday"; the award posts today, and the tool is recomputed after it (it would
    # now know the winning price).
    db.query(RecommendationSnapshot).filter(RecommendationSnapshot.solicitation_line_id == line.id).update(
        {RecommendationSnapshot.created_at: datetime.now(UTC) - timedelta(days=1)})
    db.add(AwardHistory(nsn=line.nsn, award_date=date.today(), unit_price=snapshot_bid * 2, quantity=line.quantity,
                        contract_number="SPE-HIST-1", solicitation_number=line.solicitation.solicitation_number,
                        awardee_cage="9HIST", source="award_pdf"))
    line.solicitation.return_by_date = date.today() - timedelta(days=1)
    db.commit()
    rec.recommended_bid = snapshot_bid * 5  # a post-award recompute must not count
    db.commit()
    h = client.get("/api/history").json()
    row = next(x for x in h["rows"] if x["line_id"] == line.id)
    assert row["outcome"] == "won" and row["counted"] and row["snapshot_bid"] == pytest.approx(snapshot_bid)
    assert h["stats"]["recommendation_would_have_won"] == 1 and h["stats"]["recommendation_decided"] == 1
    # No actual cost recorded: estimated margin only.
    assert h["stats"]["realized_margin_pct"] is None and h["stats"]["estimated_margin_pct"] is not None
    assert client.post(f"/api/rfq/lines/{line.id}/actual-cost", json={"actual_cost_per_unit": 8.0}, headers=csrf(client)).status_code == 200
    h = client.get("/api/history").json()
    assert h["stats"]["with_actual_cost"] == 1 and h["stats"]["realized_margin_pct"] == pytest.approx(0.2)
