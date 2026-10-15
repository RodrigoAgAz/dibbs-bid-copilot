"""The pilot loop: imports of a firm's bids, costs and outcomes; the evaluation against the firm's own process; the API,
the report and the regret check in model promotion."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.timeutil import today_et
from app.models import (
    AwardHistory,
    Organization,
    OrgSettings,
    Recommendation,
    RecommendationSnapshot,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.pilot_import import (
    import_bids,
    import_costs,
    import_outcomes,
    norm_solicitation,
    parse_date,
    read_text,
)
from app.services.pilot import collect_pilot_lines, evaluate_pilot, write_pilot_report
from dibbs_ml.pilot import _money
from tests.conftest import signup
from tests.helpers import build_world

FIX = Path(__file__).parent / "fixtures" / "pilot"


def _org(client, db, email: str, name: str, cage: str = "9PILT", n_open: int = 30, **world) -> int:
    build_world(db, n_open=n_open, **world)
    org_id = signup(client, email, org=name)["organization"]["id"]
    db.get(Organization, org_id).cage_code = cage
    db.commit()
    return org_id


def _priced(db, org_id: int) -> list[Recommendation]:
    return [r for r in db.execute(select(Recommendation).where(Recommendation.organization_id == org_id)).scalars()
            if not r.no_bid and r.recommended_bid is not None and r.landed_cost_per_unit]


def _backdate_snapshots(db, org_id: int, days: int = 3) -> None:
    db.query(RecommendationSnapshot).filter(RecommendationSnapshot.organization_id == org_id).update(
        {RecommendationSnapshot.created_at: datetime.now(UTC) - timedelta(days=days)})
    db.commit()


def _sol(db, rec: Recommendation) -> tuple[SolicitationLine, str]:
    line = db.get(SolicitationLine, rec.solicitation_line_id)
    return line, line.solicitation.solicitation_number


# ------------------------------------------------------------------------------------------------------ parsing
def test_dates_and_solicitation_numbers_are_normalized():
    assert parse_date("2026-08-03") == parse_date("08/03/2026") == parse_date("3-Aug-2026") == date(2026, 8, 3)
    assert parse_date("2026-08-03T10:00:00") == date(2026, 8, 3) and parse_date("") is None and parse_date("soon") is None
    assert norm_solicitation("spe4a6-26-t-0486") == "SPE4A626T0486"
    assert norm_solicitation(" SPE4A6 26 T 0486 ") == "SPE4A626T0486"


# ------------------------------------------------------------------------------------------------------ imports
def test_fixture_files_create_historical_rows_and_stub_lines(client, db):
    org_id = _org(client, db, "pilot-files@broker.example.com", "Files Org", n_open=4)
    out = import_bids(db, org_id, read_text(FIX / "bids_sample.csv"), source_file="bids_sample.csv")
    db.commit()
    assert {k: out[k] for k in ("rows", "matched", "historical", "created_lines", "unchanged", "skipped")} == {
        "rows": 6, "matched": 0, "historical": 4, "created_lines": 4, "unchanged": 0, "skipped": 2}
    assert len(out["errors"]) == 2 and "13-digit NSN" in out["errors"][0]
    # Two lines on one stub solicitation; the stub is closed on the bid date and never an open opportunity.
    sol = db.execute(select(Solicitation).where(Solicitation.solicitation_number == "SPE4A626T0101")).scalar_one()
    assert sol.type_code == "T" and sol.return_by_date == date(2026, 8, 3) and sol.raw_json == {"source": "pilot_import"}
    assert sorted(ln.nsn for ln in sol.lines) == ["5305010560169", "5305011209897"]
    line = next(ln for ln in sol.lines if ln.nsn == "5305011209897")
    assert line.fsc == "5305" and line.niin == "011209897" and line.quantity == 400
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == line.id)).scalar_one()
    assert rec.source == "historical" and rec.actual_bid == 0.52 and rec.actual_bid_at.date() == date(2026, 8, 3)
    assert rec.recommended_bid is None and not rec.no_bid and rec.explanation["historical"] is True
    snaps = db.execute(select(RecommendationSnapshot).where(RecommendationSnapshot.recommendation_id == rec.id)).scalars().all()
    assert [s.kind for s in snaps] == ["actual_bid"] and snaps[0].payload["imported"] is True
    assert snaps[0].created_at.date() == date(2026, 8, 3) and snaps[0].payload["source_file"] == "bids_sample.csv"
    # Re-import: nothing changes, no new snapshots.
    again = import_bids(db, org_id, read_text(FIX / "bids_sample.csv"), source_file="bids_sample.csv")
    db.commit()
    assert again["unchanged"] == 4 and again["historical"] == 0 and again["created_lines"] == 0
    assert db.query(RecommendationSnapshot).filter(RecommendationSnapshot.organization_id == org_id,
                                                   RecommendationSnapshot.kind == "actual_bid").count() == 4

    # Costs: landed per unit = cost + freight / quantity; unknown solicitations are refused with a hint.
    out = import_costs(db, org_id, read_text(FIX / "costs_sample.csv"), source_file="costs_sample.csv")
    db.commit()
    assert {k: out[k] for k in ("rows", "loaded", "unchanged", "skipped")} == {"rows": 3, "loaded": 2, "unchanged": 0, "skipped": 1}
    assert "import the bids first" in out["errors"][0]
    db.expire_all()
    assert rec.actual_cost_per_unit == pytest.approx(0.40 + 20.0 / 400) and rec.actual_cost_at.date() == date(2026, 9, 10)
    cost_snap = db.execute(select(RecommendationSnapshot).where(RecommendationSnapshot.recommendation_id == rec.id,
                                                                RecommendationSnapshot.kind == "actual_cost")).scalar_one()
    assert cost_snap.payload["unit_cost"] == 0.40 and cost_snap.payload["freight_total"] == 20.0 and cost_snap.payload["quantity"] == 400
    assert import_costs(db, org_id, read_text(FIX / "costs_sample.csv"))["unchanged"] == 2

    # Outcomes need the organization's CAGE; then one award row per line, won lines priced at the firm's bid.
    org = db.get(Organization, org_id)
    org.cage_code = None
    db.commit()
    assert "CAGE" in import_outcomes(db, org_id, read_text(FIX / "outcomes_sample.csv"))["error"]
    org.cage_code = "9PILT"
    db.commit()
    out = import_outcomes(db, org_id, read_text(FIX / "outcomes_sample.csv"), source_file="outcomes_sample.csv")
    db.commit()
    assert {k: out[k] for k in ("rows", "loaded", "already_labeled", "skipped")} == {"rows": 5, "loaded": 3, "already_labeled": 0, "skipped": 2}
    assert any("neither won nor lost" in e for e in out["errors"])
    awards = {a.nsn: a for a in db.execute(select(AwardHistory).where(AwardHistory.source == "pilot_import")).scalars()}
    won = awards["5305011209897"]
    assert won.awardee_cage == "9PILT" and won.unit_price == 0.52 and won.contract_number == "SPE4A626V0101"
    assert won.solicitation_number == "SPE4A626T0101" and won.award_date == date(2026, 8, 20) and won.quantity == 400
    lost = awards["5305010560169"]
    assert lost.awardee_cage == "1ABC2" and lost.unit_price == 1.60 and lost.contract_number == "PILOT-SPE4A626T0101"
    again = import_outcomes(db, org_id, read_text(FIX / "outcomes_sample.csv"))
    assert again["loaded"] == 0 and again["already_labeled"] == 3
    assert db.query(AwardHistory).filter(AwardHistory.source == "pilot_import").count() == 3

    # The imported rows count as the firm's outcomes but carry no pre-award decision of the tool's.
    lines, inputs = collect_pilot_lines(db, org_id)
    assert inputs["historical_rows"] == 4 and inputs["firm_bids"] == 4 and inputs["awarded"] == 3
    assert inputs["outcomes_from_firm"] == 3 and inputs["with_pre_award_decision"] == 0 and inputs["skipped_no_cost_basis"] == 1
    assert len(lines) == 2 and all(ln.historical and not ln.tool_decided and ln.cost_basis == "actual" for ln in lines)
    assert {ln.firm_won for ln in lines} == {True} and {ln.outcome_source for ln in lines} == {"firm"}
    ev = evaluate_pilot(db, org_id)
    assert ev["head_to_head"]["n"] == 0 and ev["lines"] == 2 and ev["firm_bids"] == 2 and ev["historical_lines"] == 2
    assert ev["inputs"]["firm_bids"] == 4 and ev["inputs"]["with_actual_cost"] == 2
    # History lists the firm's bids and flags them; the won/lost outcomes come from the imported awards.
    h = client.get("/api/history").json()
    rows = {r["nsn"]: r for r in h["rows"] if r["historical"]}
    assert len(rows) == 4 and rows["5305011209897"]["outcome"] == "won" and rows["5305010560169"]["outcome"] == "lost"
    assert rows["5305011209897"]["actual_cost"] == pytest.approx(0.45) and rows["5305011209897"]["recommended_bid"] is None
    assert h["stats"]["won"] == 2 and h["stats"]["lost"] == 1 and h["stats"]["recommendation_decided"] == 0


def test_bids_land_on_engine_rows_and_historical_rows_stay_off_the_dashboard(client, db):
    org_id = _org(client, db, "pilot-map@broker.example.com", "Map Org")
    recs = _priced(db, org_id)
    assert len(recs) >= 2
    a, b = recs[:2]
    line_a, sol_a = _sol(db, a)
    line_b, sol_b = _sol(db, b)
    dashed = f"{sol_a[:6]}-{sol_a[6:8]}-{sol_a[8]}-{sol_a[9:]}"  # the way DIBBS shows it
    text = ("solicitation,nsn,bid,date\n"
            f"{dashed},{line_a.nsn},{a.recommended_bid * 1.1:.4f},2026-10-01\n"
            f"{sol_b},{line_b.niin},{b.recommended_bid:.4f},2026-10-01\n")  # a NIIN is enough to find the line
    out = import_bids(db, org_id, text, source_file="bids.csv")
    db.commit()
    assert out["matched"] == 2 and out["historical"] == 0 and out["created_lines"] == 0
    db.expire_all()
    assert a.source == "engine" and a.actual_bid == pytest.approx(round(a.recommended_bid * 1.1, 4))
    kinds = [s.kind for s in db.execute(select(RecommendationSnapshot).where(RecommendationSnapshot.recommendation_id == a.id)
                                        .order_by(RecommendationSnapshot.id)).scalars()]
    assert kinds == ["recommendation", "actual_bid"]
    # Changing the bid is a new snapshot; the same bid again is not.
    text2 = f"solicitation,nsn,bid,date\n{sol_a},{line_a.nsn},{a.recommended_bid * 1.2:.4f},2026-10-02\n"
    assert import_bids(db, org_id, text2)["matched"] == 1
    assert import_bids(db, org_id, text2)["unchanged"] == 1
    db.commit()
    assert db.query(RecommendationSnapshot).filter(RecommendationSnapshot.recommendation_id == a.id,
                                                   RecommendationSnapshot.kind == "actual_bid").count() == 2

    # An open line in a supply class the organization does not watch has no engine row: the imported bid creates a
    # historical one, and the dashboard never shows it even when that class is requested.
    s = db.get(OrgSettings, org_id)
    other = next(ln for ln in db.execute(select(SolicitationLine).join(Solicitation)
                                         .where(Solicitation.return_by_date >= today_et())).scalars()
                 if ln.fsc != line_a.fsc)
    s.fsc_watchlist = [f for f in s.fsc_watchlist if f != other.fsc]
    db.query(Recommendation).filter(Recommendation.organization_id == org_id).filter(
        Recommendation.solicitation_line_id.in_([ln.id for ln in db.execute(select(SolicitationLine).where(
            SolicitationLine.fsc == other.fsc)).scalars()])).delete(synchronize_session=False)
    db.commit()
    out = import_bids(db, org_id, f"solicitation,nsn,bid,date\n{other.solicitation.solicitation_number},{other.nsn},5.00,2026-10-03\n")
    db.commit()
    assert out["historical"] == 1 and out["created_lines"] == 0
    rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                  Recommendation.solicitation_line_id == other.id)).scalar_one()
    assert rec.source == "historical"
    d = client.get("/api/dashboard", params={"fsc": [other.fsc]}).json()
    assert other.id not in {x["line_id"] for x in d["lines"]}
    assert client.get("/api/portfolio").status_code == 200
    # Once the engine prices such a line it becomes an engine row (the bid stays).
    from app.services.recommendations import refresh_line_for_org

    refresh_line_for_org(db, org_id, other)
    db.commit()
    db.expire_all()
    assert rec.source == "engine" and rec.actual_bid == 5.0


# --------------------------------------------------------------------------------------------------- evaluation
def test_evaluation_compares_the_tool_with_the_firm_on_awarded_lines(client, db):
    org_id = _org(client, db, "pilot-eval@broker.example.com", "Pilot Org")
    recs = _priced(db, org_id)
    assert len(recs) >= 4, "need four priced lines"
    _backdate_snapshots(db, org_id)
    today = today_et()
    bid_day, award_day = (today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()
    A, B, C, D = recs[:4]
    la, sa = _sol(db, A)
    lb, sb = _sol(db, B)
    lc, sc = _sol(db, C)
    ld, sd = _sol(db, D)
    bid_a, bid_b, bid_c = round(A.recommended_bid * 0.9, 4), round(B.recommended_bid * 1.2, 4), round(C.recommended_bid, 4)
    w_b, w_d = round(B.recommended_bid * 1.1, 4), round(D.recommended_bid * 1.3, 4)
    cost_a = round(A.landed_cost_per_unit * 0.8, 4)
    assert import_bids(db, org_id, "solicitation,nsn,bid,date\n"
                       f"{sa},{la.nsn},{bid_a},{bid_day}\n{sb},{lb.nsn},{bid_b},{bid_day}\n{sc},{lc.nsn},{bid_c},{bid_day}\n")["matched"] == 3
    # A and C won at their bids; B lost to a price between the tool's bid and the firm's; D was skipped by the firm.
    out = import_outcomes(db, org_id, "solicitation,nsn,outcome,winning price,award date,awardee\n"
                          f"{sa},{la.nsn},won,,{award_day},\n{sb},{lb.nsn},lost,{w_b},{award_day},1OTHR\n"
                          f"{sc},{lc.nsn},won,{bid_c},{award_day},\n{sd},{ld.nsn},lost,{w_d},{award_day},1OTHR\n")
    assert out["loaded"] == 4, out
    assert import_costs(db, org_id, f"solicitation,nsn,unit cost,date paid\n{sa},{la.nsn},{cost_a},{today.isoformat()}\n")["loaded"] == 1
    db.commit()

    lines, inputs = collect_pilot_lines(db, org_id)
    assert inputs["awarded"] == 4 and inputs["with_pre_award_decision"] == 4 and inputs["outcomes_from_firm"] == 4
    by = {ln.line_id: ln for ln in lines}
    assert by[la.id].cost_basis == "actual" and by[la.id].landed == pytest.approx(cost_a) and by[la.id].firm_won is True
    assert by[lb.id].cost_basis == "estimate" and by[lb.id].landed == pytest.approx(B.landed_cost_per_unit) and by[lb.id].firm_won is False
    assert by[ld.id].actual_bid is None and by[ld.id].rec_bid == pytest.approx(D.recommended_bid) and by[ld.id].confidence in ("high", "medium", "low", "none")

    ev = evaluate_pilot(db, org_id)
    h = ev["head_to_head"]
    assert h["n"] == 3 and h["n_real_cost"] == 1 and h["n_estimated_cost"] == 2 and h["n_outcome_inferred"] == 0
    firm_a = (bid_a - cost_a) * la.quantity  # won at a bid below the tool's: the tool's higher bid is scored as a loss
    firm_c = tool_c = (bid_c - C.landed_cost_per_unit) * lc.quantity  # won at the tool's bid: both win
    tool_b = (B.recommended_bid - B.landed_cost_per_unit) * lb.quantity  # the firm overbid and lost; the tool's bid was under W
    assert h["firm"] == pytest.approx({"bids": 3, "no_bids": 0, "wins": 2, "profit": round(firm_a + firm_c, 2),
                                       "hours": round(3 * 0.33, 2), "profit_per_hour": round((firm_a + firm_c) / 0.99, 2),
                                       "regret": round((w_b - B.landed_cost_per_unit) * lb.quantity, 2), "win_rate": round(2 / 3, 4)}, abs=0.02)
    assert h["tool"]["bids"] == 3 and h["tool"]["wins"] == 2 and h["tool"]["no_bids"] == 0
    assert h["tool"]["profit"] == pytest.approx(tool_b + tool_c, abs=0.02)
    assert h["tool"]["regret"] == pytest.approx(firm_a + (w_b - B.landed_cost_per_unit) * lb.quantity - tool_b, abs=0.02)
    assert h["increment"] == pytest.approx(tool_b - firm_a, abs=0.02)
    assert h["increment_per_firm_hour"] == pytest.approx((tool_b - firm_a) / 0.99, abs=0.05)
    assert h["oracle_profit"] == pytest.approx(firm_a + (w_b - B.landed_cost_per_unit) * lb.quantity + firm_c, abs=0.02)
    real = ev["head_to_head_real_cost"]
    assert real["n"] == 1 and real["firm"]["profit"] == pytest.approx(firm_a, abs=0.01) and real["tool"]["profit"] == 0
    # The tool as a policy also bids on the line the firm skipped, and wins it under the posted price.
    pol = ev["policy"]
    tool_d = (D.recommended_bid - D.landed_cost_per_unit) * ld.quantity
    assert pol["n"] == 4 and pol["firm"]["bids"] == 3 and pol["tool"]["bids"] == 4 and pol["tool"]["wins"] == 3
    assert pol["tool"]["profit"] == pytest.approx(tool_b + tool_c + tool_d, abs=0.02)
    assert sum(b["n"] for b in ev["by_fsc"]) == 3 and sum(b["n"] for b in ev["by_confidence"]) == 3
    assert {b["key"] for b in ev["by_fsc"]} == {la.fsc, lb.fsc, lc.fsc}
    assert ev["cost_model"]["quotes"] == 0 and ev["contestability"]["own_lines"]["n"] == 4  # D has a P(winnable) too
    assert any("losing bids" in c for c in ev["caveats"]) and any("too few" in c for c in ev["caveats"])
    assert any("own records" in c for c in ev["caveats"]) and any("no recorded purchase cost" in c for c in ev["caveats"])

    # The API returns the same evaluation for the signed-in organization only.
    r = client.get("/api/history/pilot")
    assert r.status_code == 200
    body = r.json()
    assert body["head_to_head"]["n"] == 3 and body["head_to_head"]["increment"] == pytest.approx(h["increment"])
    assert body["org_name"] == "Pilot Org" and body["hours_per_line"] == 0.33 and "scored" not in body
    # The report states the numbers and their caveats.
    out_dir = Path(tempfile.mkdtemp(prefix="pilot-report-"))
    path, _ = write_pilot_report(db, org_id, out_dir)
    text = path.read_text(encoding="utf-8")
    assert path.name == "pilot_pilot_org.md" and "# Pilot: Pilot Org" in text and "## Head to head" in text
    assert sa in text and "## Caveats" in text and "Only the winning price is observed" in text
    assert _money(h["increment"]) in text

    # Another organization sees nothing of it.
    signup(client, "other@broker.example.com", org="Other Org")
    assert client.get("/api/history/pilot").json()["head_to_head"]["n"] == 0


def test_outcome_is_inferred_from_the_price_without_a_cage(client, db):
    org_id = _org(client, db, "pilot-nocage@broker.example.com", "No Cage Org", n_open=20)
    db.get(Organization, org_id).cage_code = None
    db.commit()
    recs = _priced(db, org_id)
    _backdate_snapshots(db, org_id)
    rec = recs[0]
    line, sol = _sol(db, rec)
    rec.actual_bid, rec.actual_bid_at = round(rec.recommended_bid * 1.05, 4), datetime.now(UTC) - timedelta(days=2)
    db.add(AwardHistory(nsn=line.nsn, award_date=today_et() - timedelta(days=1), unit_price=rec.actual_bid, quantity=line.quantity,
                        contract_number="SPE-NC-1", solicitation_number=sol, awardee_cage=None, source="award_pdf"))
    db.commit()
    ev = evaluate_pilot(db, org_id)
    h = ev["head_to_head"]
    assert h["n"] == 1 and h["n_outcome_inferred"] == 1 and h["firm"]["wins"] == 1 and h["tool"]["wins"] == 1
    assert any("inferred" in c for c in ev["caveats"])


def test_lines_from_before_snapshots_fall_back_to_the_row_updated_before_the_award(client, db):
    """The demo seed and lines priced before engine v9 have no snapshots: like History, the recommendation row counts
    when nothing touched it after the award; a row recomputed after the award does not."""
    org_id = _org(client, db, "pilot-old@broker.example.com", "Old Org", n_open=6)
    cage = "9PILT"
    today = today_et()
    old = datetime.now(UTC) - timedelta(days=10)
    lines = list(db.execute(select(SolicitationLine).join(Solicitation).where(Solicitation.return_by_date >= today)).scalars())
    db.query(Recommendation).filter(Recommendation.organization_id == org_id).delete(synchronize_session=False)
    db.query(RecommendationSnapshot).filter(RecommendationSnapshot.organization_id == org_id).delete(synchronize_session=False)
    rows = []
    for i, ln in enumerate(lines[:2]):
        rec = Recommendation(organization_id=org_id, solicitation_line_id=ln.id, landed_cost_per_unit=6.0, provisional=False,
                             recommended_bid=9.0, no_bid=False, explanation={"confidence": "high"}, actual_bid=12.0, actual_bid_at=old,
                             created_at=old, updated_at=old if i == 0 else datetime.now(UTC))  # the second was recomputed since
        db.add(rec)
        db.add(AwardHistory(nsn=ln.nsn, award_date=today - timedelta(days=1), unit_price=10.0, quantity=ln.quantity,
                            contract_number=f"SPE-OLD-{i}", solicitation_number=ln.solicitation.solicitation_number,
                            awardee_cage="1OTHR", source="award_pdf"))
        rows.append(rec)
    db.commit()
    db.query(Recommendation).filter(Recommendation.id == rows[0].id).update({Recommendation.updated_at: old})  # onupdate ran
    db.commit()
    assert db.get(Organization, org_id).cage_code == cage
    lines_out, inputs = collect_pilot_lines(db, org_id)
    assert inputs["awarded"] == 2 and inputs["with_pre_award_decision"] == 1 and inputs["decisions_from_updated_at"] == 1
    decided = [ln for ln in lines_out if ln.tool_decided]
    assert len(decided) == 1 and decided[0].line_id == rows[0].solicitation_line_id and decided[0].rec_bid == 9.0
    assert decided[0].confidence == "high" and decided[0].cost_basis == "estimate" and decided[0].landed == 6.0
    ev = evaluate_pilot(db, org_id)
    h = ev["head_to_head"]
    assert h["n"] == 1 and h["tool"]["wins"] == 1 and h["firm"]["wins"] == 0
    assert h["tool"]["profit"] == pytest.approx(3.0 * lines[0].quantity) and h["increment"] == h["tool"]["profit"]
    assert any("predate decision snapshots" in c for c in ev["caveats"])


# ------------------------------------------------------------------------------------------- model promotion
def test_regret_check_enters_the_promotion_decision(client, db, monkeypatch):
    from app.models import ModelRun
    from app.services.training import train_and_maybe_promote

    org_id = _org(client, db, "pilot-train@broker.example.com", "Train Org", n_open=24, n_nsns=80, seed=9)
    recs = _priced(db, org_id)
    assert len(recs) >= 3
    _backdate_snapshots(db, org_id)
    today = today_et()
    for i, rec in enumerate(recs[:4]):
        line, sol = _sol(db, rec)
        rec.actual_bid, rec.actual_bid_at = round(rec.recommended_bid * 1.2, 4), datetime.now(UTC) - timedelta(days=2)
        db.add(AwardHistory(nsn=line.nsn, award_date=today - timedelta(days=1), unit_price=round(rec.recommended_bid * 1.05, 4),
                            quantity=line.quantity, contract_number=f"SPE-PIL-{i}", solicitation_number=sol,
                            awardee_cage="1OTHR", source="award_pdf"))
    db.commit()
    monkeypatch.setattr("dibbs_ml.pilot.REGRET_MIN_LINES", 2)
    first = train_and_maybe_promote(db, as_of=today, force_promote=True)
    db.commit()
    m1 = db.get(ModelRun, first["model_run_id"]).metrics["pilot_regret"]
    assert m1["passed"] is True and "no active model" in m1.get("note", "")  # nothing to compare with yet
    second = train_and_maybe_promote(db, as_of=today)
    db.commit()
    m2 = db.get(ModelRun, second["model_run_id"]).metrics["pilot_regret"]
    n = min(4, len(recs))
    assert m2["n"] == n and m2["enough"] is True and m2["min_lines"] == 2 and m2["real_cost_lines"] == 0
    assert m2["candidate"]["n"] == n and m2["active"]["n"] == n and m2["candidate"]["bids"] <= n
    assert m2["candidate"]["regret"] >= 0 and m2["active"]["regret"] >= 0 and isinstance(m2["passed"], bool)
    assert m2["test_window"][1] == today.isoformat()
    # A worse regret blocks promotion even when pinball improves; the reason says so.
    from dibbs_ml.train import should_promote

    metrics = {"pinball_mean": 0.1, "active_model_on_same_test": {"pinball_mean": 0.2},
               "pilot_regret": {**m2, "passed": False, "candidate": {**m2["candidate"], "regret": 1000.0},
                                "active": {**m2["active"], "regret": 500.0}}}
    ok, reason = should_promote(metrics)
    assert not ok and "awarded pilot lines" in reason and "$1,000" in reason
