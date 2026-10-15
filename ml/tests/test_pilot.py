"""Pilot maths: win rule, per-line profit and regret, aggregates per hour, the promotion check, the report."""

from __future__ import annotations

from datetime import date

import pytest

from dibbs_ml.pilot import (
    REGRET_MIN_LINES,
    PilotLine,
    bid_wins,
    caveats,
    compare_policies,
    firm_outcome,
    policy_regret,
    render_report,
    score_line,
    summarize,
)
from dibbs_ml.train import should_promote


def line(**kw) -> PilotLine:
    base = dict(line_id=1, solicitation_number="SPE4A626T0001", nsn="5305011209897", fsc="5305", quantity=100.0,
                winning_price=10.0, landed=6.0, cost_basis="actual", rec_bid=9.0, tool_decided=True, no_bid=False,
                actual_bid=None, firm_won=None, confidence="high", award_date=date(2026, 10, 1))
    base.update(kw)
    return PilotLine(**base)


def test_win_rule_uses_only_the_winning_price():
    assert bid_wins(9.99, 10.0, firm_won=False) and not bid_wins(10.0, 10.0, firm_won=False) and not bid_wins(10.01, 10.0, False)
    # The firm won at 10: every competitor was above it, so a bid at 10 also wins; above it is unknown and scored as a loss.
    assert bid_wins(10.0, 10.0, firm_won=True) and not bid_wins(10.01, 10.0, firm_won=True)
    assert not bid_wins(None, 10.0, True)


def test_firm_outcome_is_inferred_only_without_a_cage():
    assert firm_outcome(line(actual_bid=12.0, firm_won=False)) == (False, False)
    assert firm_outcome(line(actual_bid=10.0, firm_won=None)) == (True, True)  # at the winning price: the winner
    assert firm_outcome(line(actual_bid=12.0, firm_won=None)) == (False, True)
    assert firm_outcome(line(actual_bid=None)) == (False, False)


def test_score_line_profit_and_regret():
    # The firm won at 10 (W = its bid); the tool recommended 11: scored as a loss, regret = everything.
    s = score_line(line(actual_bid=10.0, firm_won=True, rec_bid=11.0))
    assert s.oracle == 400 and s.firm_profit == 400 and s.tool_profit == 0 and not s.tool_wins
    assert s.firm_regret == 0 and s.tool_regret == 400 and s.increment == -400
    # The firm overbid at 12 and lost to 10; the tool's 9 would have won: the increment is the tool's profit.
    s = score_line(line(actual_bid=12.0, firm_won=False, rec_bid=9.0))
    assert s.firm_profit == 0 and s.tool_wins and s.tool_profit == 300 and s.firm_regret == 400 and s.tool_regret == 100 and s.increment == 300
    # A no-bid earns nothing; a line the firm skipped has no firm regret or increment.
    s = score_line(line(actual_bid=12.0, firm_won=False, rec_bid=None, no_bid=True))
    assert s.tool_profit == 0 and s.tool_regret == 400 and s.increment == -0.0
    s = score_line(line(actual_bid=None, rec_bid=9.0))
    assert s.tool_profit == 300 and s.firm_regret is None and s.increment is None
    # Winning price below the landed cost: the best bid is no bid, every bid that wins loses money.
    s = score_line(line(winning_price=5.0, actual_bid=5.0, firm_won=True, rec_bid=None, no_bid=True))
    assert s.oracle == 0 and s.firm_profit == -100 and s.firm_regret == 100 and s.tool_regret == 0


def test_summarize_profit_per_hour_and_buckets():
    lines = [
        line(line_id=1, actual_bid=10.0, firm_won=True, rec_bid=11.0),  # firm +400, tool 0
        line(line_id=2, actual_bid=12.0, firm_won=False, rec_bid=9.0, fsc="5310", confidence="medium", cost_basis="estimate"),  # firm 0, tool +300
        line(line_id=3, actual_bid=10.0, firm_won=True, rec_bid=10.0),  # both +400
        line(line_id=4, actual_bid=None, rec_bid=9.0, winning_price=10.0),  # skipped by the firm; the tool wins +300
        line(line_id=5, actual_bid=12.0, firm_won=False, rec_bid=None, tool_decided=False),  # no decision before the award
    ]
    s = summarize(lines, hours_per_line=0.5)
    h = s["head_to_head"]
    assert s["lines"] == 5 and s["lines_decided"] == 4 and s["firm_bids"] == 4
    assert h["n"] == 3 and h["n_real_cost"] == 2 and h["n_estimated_cost"] == 1
    assert h["firm"] == {"bids": 3, "no_bids": 0, "wins": 2, "profit": 800.0, "hours": 1.5, "profit_per_hour": 533.33,
                         "regret": 400.0, "win_rate": 0.6667}
    assert h["tool"] == {"bids": 3, "no_bids": 0, "wins": 2, "profit": 700.0, "hours": 1.5, "profit_per_hour": 466.67,
                         "regret": 500.0, "win_rate": 0.6667}
    assert h["oracle_profit"] == 1200.0 and h["increment"] == -100.0 and h["increment_per_firm_hour"] == pytest.approx(-66.67)
    assert h["profit_per_hour_delta"] == pytest.approx(-66.66, abs=0.01)
    real = s["head_to_head_real_cost"]
    assert real["n"] == 2 and real["firm"]["profit"] == 800.0 and real["tool"]["profit"] == 400.0
    pol = s["policy"]
    assert pol["n"] == 4 and pol["firm"]["bids"] == 3 and pol["tool"]["bids"] == 4 and pol["tool"]["profit"] == 1000.0
    assert pol["firm"]["regret"] == 800.0  # the skipped line's hindsight profit counts against the firm
    assert [b["key"] for b in s["by_fsc"]] == ["5305", "5310"] and s["by_fsc"][1]["increment"] == 300.0
    assert [b["key"] for b in s["by_confidence"]] == ["high", "medium"] and s["by_confidence"][0]["n"] == 2
    assert len(s["scored"]) == 5 and s["scored"][0]["line"]["award_date"] == "2026-10-01"
    # Empty input still yields every block (the History page renders before any import).
    e = summarize([], 0.33)
    assert e["head_to_head"]["n"] == 0 and e["head_to_head"]["firm"]["profit_per_hour"] is None and e["by_fsc"] == []


def test_compare_policies_and_the_promotion_gate():
    lines = [line(line_id=i, actual_bid=12.0, firm_won=False) for i in range(5)]
    better = [9.0] * 5  # wins everything at a thin margin: profit 300 each, regret 100 each
    worse = [9.9] * 4 + [None]  # one no-bid: regret 400 there, 10 elsewhere
    pr = compare_policies(better, worse, lines, min_lines=5)
    assert pr["n"] == 5 and pr["enough"] and pr["candidate"]["regret"] == 500.0 and pr["active"]["regret"] == 440.0
    assert pr["candidate"]["bids"] == 5 and pr["active"]["bids"] == 4 and pr["active"]["wins"] == 4
    assert not pr["passed"]  # 500 > 440 x 1.05
    assert compare_policies(worse, better, lines, min_lines=5)["passed"]
    assert compare_policies(better, worse, lines, min_lines=6)["passed"] and not compare_policies(better, worse, lines, min_lines=6)["enough"]
    assert compare_policies(better, better, lines, min_lines=1)["passed"]
    assert policy_regret([None] * 5, lines)["regret"] == 2000.0 and policy_regret([None] * 5, lines)["bids"] == 0
    with pytest.raises(ValueError):
        policy_regret([9.0], lines)

    base = {"pinball_mean": 0.20, "active_model_on_same_test": {"pinball_mean": 0.21}}
    assert should_promote(base) == (True, "pinball 0.2000 < active 0.2100")
    assert should_promote({**base, "pilot_regret": pr})[0] is False
    assert "awarded pilot lines" in should_promote({**base, "pilot_regret": pr})[1]
    ok, reason = should_promote({**base, "pilot_regret": compare_policies(worse, better, lines, min_lines=5)})
    assert ok and "regret on 5 awarded pilot lines" in reason
    assert should_promote({**base, "pilot_regret": {"n": 3, "enough": False, "passed": True}}) == (True, "pinball 0.2000 < active 0.2100")
    assert should_promote({"pinball_mean": 0.22, "active_model_on_same_test": {"pinball_mean": 0.21}, "pilot_regret": pr})[0] is False
    assert should_promote({"pinball_mean": 0.22})[0] is True  # no active model: nothing to compare with
    assert REGRET_MIN_LINES == 30


def test_report_states_numbers_and_caveats():
    lines = [line(line_id=1, actual_bid=10.0, firm_won=True, rec_bid=11.0, cost_basis="estimate"),
             line(line_id=2, actual_bid=12.0, firm_won=None, rec_bid=9.0)]
    s = summarize(lines, 0.33)
    inputs = {"recommendations": 2, "outcomes_from_firm": 1}
    text = render_report(s, "Atlas Fasteners", date(2026, 10, 15), inputs=inputs,
                         cost_model={"quotes": 3, "tiers": {"nsn": {"n": 2, "mdape": 0.1, "within25": 1.0, "coverage_80": 0.5, "prior_mdape": 0.3}}},
                         contestability={"by_fsc": [{"fsc": "5305", "n": 40, "brier": 0.12, "baseline_brier": 0.2, "reseller_share": 0.6}],
                                         "own_lines": {"n": 2, "brier": 0.1, "baseline_brier": 0.3}})
    assert text.startswith("# Pilot: Atlas Fasteners") and "| Your process | 2 | 1 |" in text and "| The tool | 2 (+0 no-bids) | 1 |" in text
    assert "**Increment: -$100 of completed profit, -$152 per hour you spent.**" in text
    assert "| nsn | 2 | 10% | 100% | 50% | 30% |" in text and "| 5305 | 40 | 60% | 0.120 | 0.200 |" in text
    assert "Brier 0.100" in text and "| SPE4A626T0001 |" in text and "## Lines" in text
    cs = caveats(s, inputs)
    assert any("losing bids" in c for c in cs) and any("1 of 2 compared lines have no recorded purchase cost" in c for c in cs)
    assert any("inferred from the price" in c for c in cs) and any("own records" in c for c in cs) and any("too few" in c for c in cs)
