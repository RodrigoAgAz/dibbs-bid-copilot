"""Pilot evaluation: does following the tool make a broker more money per hour than its own process, on real lines?

Inputs are awarded lines on which the tool's decision was recorded (a decision snapshot) BEFORE the award posted,
with the firm's own bid, the landed cost (what the firm actually paid when it recorded that, else the estimate the
recommendation was priced against, flagged) and the outcome. Per line, with W the verified winning unit price,
qty the quantity and landed the landed cost per unit:

    oracle     profit of the best bid with hindsight: (W - landed) x qty when W > landed, else 0 (no bid)
    firm       (actual_bid - landed) x qty if the firm won, else 0
    tool       (rec_bid - landed) x qty if the recommended bid would have won, else 0; a no-bid earns 0
    regret     oracle - profit, for each policy: what it left on the table
    increment  tool - firm: the completed profit the tool would have added on that line

Would the recommended bid have won? Only the winning price is observed, never the losing bids, so the rule is the one
used everywhere in this codebase: a bid wins iff it is below the winning price (ties lose). When the firm itself won,
W is the firm's bid and every competitor was above it, so a recommended bid at or below W also wins; a recommended bid
ABOVE the firm's winning bid is scored as a loss although it might have won too (the second-lowest bid is unknown).
That bias runs against the tool and the report says so.

Broker hours: every line pursued (a bid placed) costs `hours_per_line` (org_settings.pursuit_hours_per_line); a no-bid
costs nothing. Profit per hour = completed profit / hours. "Completed" means the award posted and the outcome is known.

Pure Python. The backend (app/services/pilot.py) collects the lines, adds the tenant cost model's leave-one-out error
and the contestability Brier score, and writes ml/reports/pilot_<org>.md.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date

# Model promotion (dibbs_ml.train.should_promote): the regret check applies once this many awarded pilot lines with a
# pre-award decision fall in the candidate's test window, and a candidate passes when its total regret on them is at
# most this much worse than the active model's. Below the minimum, pinball loss alone decides (see DECISIONS.md).
REGRET_MIN_LINES = 30
REGRET_TOLERANCE = 0.05
EPS = 1e-9


@dataclass
class PilotLine:
    """One awarded line as the evaluation sees it."""

    line_id: int
    solicitation_number: str
    nsn: str
    fsc: str
    quantity: float
    winning_price: float
    landed: float  # per unit: what the firm paid (actual) or the estimate the recommendation was priced against
    cost_basis: str  # "actual" | "estimate"
    rec_bid: float | None  # the bid shown before the award; None when the tool said no bid or had no price
    tool_decided: bool  # the tool had a bid or an explicit no-bid before the award posted
    no_bid: bool
    actual_bid: float | None  # the firm's bid per unit; None when the firm skipped the line
    firm_won: bool | None  # None: unknown (no CAGE on the organization); inferred from the price then
    confidence: str | None = None
    outcome_source: str = "dibbs"  # "dibbs" (award labeling) | "firm" (imported outcome)
    award_date: date | None = None
    p_winnable: float | None = None  # contestability shown before the award, when recorded
    awardee_is_reseller: bool | None = None
    historical: bool = False  # a line the tool never priced (imported bid only)


@dataclass
class ScoredLine:
    line: PilotLine
    oracle: float
    firm_won: bool
    firm_inferred: bool
    tool_wins: bool
    tool_profit: float
    firm_profit: float
    tool_regret: float
    firm_regret: float | None  # None: the firm did not bid
    increment: float | None  # tool - firm; None when the firm did not bid

    def to_dict(self) -> dict:
        d = asdict(self)
        d["line"]["award_date"] = self.line.award_date.isoformat() if self.line.award_date else None
        return d


def bid_wins(bid: float | None, winning_price: float, firm_won: bool) -> bool:
    """A counterfactual bid wins iff it is below the winning price; at the winning price only if that price was the
    firm's own (every competitor was above it)."""
    if bid is None:
        return False
    if bid < winning_price - EPS:
        return True
    return bool(firm_won) and bid <= winning_price + EPS


def firm_outcome(line: PilotLine) -> tuple[bool, bool]:
    """(won, inferred). Without the organization's CAGE the outcome is inferred: a bid at or below the winning price
    must have been the winner (a lower bid would itself have set the winning price)."""
    if line.firm_won is not None:
        return bool(line.firm_won), False
    if line.actual_bid is None:
        return False, False
    return line.actual_bid <= line.winning_price * (1.0 + 1e-6), True


def oracle_profit(line: PilotLine) -> float:
    return max(line.winning_price - line.landed, 0.0) * line.quantity


def score_line(line: PilotLine) -> ScoredLine:
    oracle = oracle_profit(line)
    won, inferred = firm_outcome(line)
    firm_profit = (line.actual_bid - line.landed) * line.quantity if (won and line.actual_bid is not None) else 0.0
    tool_wins = line.tool_decided and bid_wins(line.rec_bid, line.winning_price, won)
    tool_profit = (line.rec_bid - line.landed) * line.quantity if tool_wins else 0.0
    bid_placed = line.actual_bid is not None
    return ScoredLine(
        line=line, oracle=oracle, firm_won=won, firm_inferred=inferred, tool_wins=tool_wins,
        tool_profit=tool_profit, firm_profit=firm_profit, tool_regret=oracle - tool_profit,
        firm_regret=(oracle - firm_profit) if bid_placed else None,
        increment=(tool_profit - firm_profit) if bid_placed else None,
    )


def score_lines(lines: Sequence[PilotLine]) -> list[ScoredLine]:
    return [score_line(ln) for ln in lines]


# --------------------------------------------------------------------------------------------------- aggregates
def _policy(rows: Sequence[ScoredLine], bids: int, wins: int, profit: float, regret: float, hours_per_line: float,
            no_bids: int = 0) -> dict:
    hours = bids * hours_per_line
    return {
        "bids": bids, "no_bids": no_bids, "wins": wins, "profit": round(profit, 2), "hours": round(hours, 2),
        "profit_per_hour": round(profit / hours, 2) if hours > 0 else None,
        "regret": round(regret, 2),
        "win_rate": round(wins / bids, 4) if bids else None,
    }


def compare(rows: Sequence[ScoredLine], hours_per_line: float) -> dict:
    """Firm vs tool on the same lines: completed profit, hours, profit per hour and regret."""
    firm_bids = [s for s in rows if s.line.actual_bid is not None]
    firm = _policy(
        rows, bids=len(firm_bids), wins=sum(1 for s in firm_bids if s.firm_won), profit=sum(s.firm_profit for s in rows),
        regret=sum(s.oracle - s.firm_profit for s in rows), hours_per_line=hours_per_line,
    )
    tool_bids = [s for s in rows if s.line.tool_decided and s.line.rec_bid is not None]
    tool = _policy(
        rows, bids=len(tool_bids), wins=sum(1 for s in tool_bids if s.tool_wins), profit=sum(s.tool_profit for s in rows),
        regret=sum(s.tool_regret for s in rows), hours_per_line=hours_per_line,
        no_bids=sum(1 for s in rows if s.line.tool_decided and s.line.rec_bid is None),
    )
    increment = tool["profit"] - firm["profit"]
    return {
        "n": len(rows),
        "n_real_cost": sum(1 for s in rows if s.line.cost_basis == "actual"),
        "n_estimated_cost": sum(1 for s in rows if s.line.cost_basis != "actual"),
        "n_outcome_inferred": sum(1 for s in rows if s.firm_inferred),
        "oracle_profit": round(sum(s.oracle for s in rows), 2),
        "firm": firm,
        "tool": tool,
        "increment": round(increment, 2),
        # The tool's extra profit per hour the firm actually spent: what the pilot is meant to prove or disprove.
        "increment_per_firm_hour": round(increment / firm["hours"], 2) if firm["hours"] > 0 else None,
        # Both policies' profit over the same effort: the tool's hours when its no-bids are followed.
        "profit_per_hour_delta": (round(tool["profit_per_hour"] - firm["profit_per_hour"], 2)
                                  if tool["profit_per_hour"] is not None and firm["profit_per_hour"] is not None else None),
    }


def buckets(rows: Sequence[ScoredLine], key: Callable[[ScoredLine], str], hours_per_line: float) -> list[dict]:
    groups: dict[str, list[ScoredLine]] = {}
    for s in rows:
        groups.setdefault(key(s), []).append(s)
    out = []
    for k in sorted(groups):
        c = compare(groups[k], hours_per_line)
        out.append({
            "key": k, "n": c["n"], "firm_bids": c["firm"]["bids"], "firm_wins": c["firm"]["wins"],
            "firm_profit": c["firm"]["profit"], "firm_regret": c["firm"]["regret"], "tool_bids": c["tool"]["bids"],
            "tool_wins": c["tool"]["wins"], "tool_profit": c["tool"]["profit"], "tool_regret": c["tool"]["regret"],
            "increment": c["increment"], "increment_per_firm_hour": c["increment_per_firm_hour"],
        })
    return out


CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}


def summarize(lines: Sequence[PilotLine], hours_per_line: float) -> dict:
    """Everything the report and the History page show, from the scored lines."""
    scored = score_lines(lines)
    decided = [s for s in scored if s.line.tool_decided]
    head = [s for s in decided if s.line.actual_bid is not None]
    by_conf = buckets(head, lambda s: s.line.confidence or "unknown", hours_per_line)
    by_conf.sort(key=lambda b: CONFIDENCE_ORDER.get(b["key"], 9))
    return {
        "hours_per_line": hours_per_line,
        "lines": len(lines),
        "lines_decided": len(decided),
        "firm_bids": sum(1 for s in scored if s.line.actual_bid is not None),
        "historical_lines": sum(1 for s in scored if s.line.historical),
        # Lines the firm bid on where the tool had decided before the award: the fair head-to-head.
        "head_to_head": compare(head, hours_per_line),
        # The same, only where the firm recorded what it actually paid (no estimated cost in the profit).
        "head_to_head_real_cost": compare([s for s in head if s.line.cost_basis == "actual"], hours_per_line),
        # The tool as a policy over every line it decided, including lines the firm skipped (its bids there are
        # counterfactual wins or losses against the posted winning price).
        "policy": compare(decided, hours_per_line),
        "by_fsc": buckets(head, lambda s: s.line.fsc, hours_per_line),
        "by_confidence": by_conf,
        "scored": [s.to_dict() for s in scored],
    }


# ------------------------------------------------------------------------------------------- model promotion check
def policy_regret(bids: Sequence[float | None], lines: Sequence[PilotLine]) -> dict:
    """Realized regret of a bidding policy (one bid or None per line) against the posted winning prices."""
    if len(bids) != len(lines):
        raise ValueError("one bid (or None) per line")
    regret = profit = oracle = 0.0
    n_bids = wins = 0
    for b, ln in zip(bids, lines, strict=True):
        o = oracle_profit(ln)
        won, _ = firm_outcome(ln)
        if b is not None:
            n_bids += 1
        w = bid_wins(b, ln.winning_price, won)
        p = (b - ln.landed) * ln.quantity if w else 0.0
        wins += int(w)
        profit += p
        oracle += o
        regret += o - p
    n = len(lines)
    return {"n": n, "bids": n_bids, "wins": wins, "profit": round(profit, 2), "oracle": round(oracle, 2),
            "regret": round(regret, 2), "regret_per_line": round(regret / n, 2) if n else None}


def compare_policies(candidate_bids: Sequence[float | None], active_bids: Sequence[float | None],
                     lines: Sequence[PilotLine], min_lines: int = REGRET_MIN_LINES,
                     tolerance: float = REGRET_TOLERANCE) -> dict:
    """The promotion check: the candidate model's bids must not leave materially more on the table than the active
    model's on the same awarded pilot lines. `passed` is True when there are too few lines to judge."""
    cand, act = policy_regret(candidate_bids, lines), policy_regret(active_bids, lines)
    n = len(lines)
    enough = n >= min_lines
    passed = (not enough) or cand["regret"] <= act["regret"] * (1.0 + tolerance) + EPS
    return {"n": n, "min_lines": min_lines, "tolerance": tolerance, "enough": enough, "passed": bool(passed),
            "candidate": cand, "active": act}


# ------------------------------------------------------------------------------------------------------- report
def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.0f}%"


def caveats(summary: dict, inputs: dict | None = None) -> list[str]:
    inputs = inputs or {}
    h = summary["head_to_head"]
    out = [
        "Only the winning price is observed, never the losing bids. A recommended bid below the winning price is scored "
        "as a win; above the firm's own winning bid it is scored as a loss although it might have won (the second-lowest "
        "bid is unknown). The bias runs against the tool.",
        "Profit is bid minus landed cost times quantity on won lines; it ignores fulfilment losses, payment timing and "
        "the lines each policy would have looked at but not bid on. Hours are the bids placed times the pursuit hours "
        "per line from Settings, so the firm's time on lines it looked at and skipped is not counted.",
    ]
    if h["n_estimated_cost"]:
        out.append(f"{h['n_estimated_cost']} of {h['n']} compared lines have no recorded purchase cost: their profit uses "
                   "the landed cost the recommendation was priced against, an estimate. The real-cost table excludes them.")
    if h.get("n_outcome_inferred"):
        out.append(f"{h['n_outcome_inferred']} outcomes were inferred from the price (the organization has no CAGE code): "
                   "a bid at or below the winning price counts as won.")
    if inputs.get("outcomes_from_firm"):
        out.append(f"{inputs['outcomes_from_firm']} outcomes come from the firm's own records, not from DIBBS award data; "
                   "they are trusted for this evaluation only and never enter model training.")
    if inputs.get("decisions_from_updated_at"):
        out.append(f"{inputs['decisions_from_updated_at']} decisions predate decision snapshots and are taken from the "
                   "recommendation row itself, which was last updated before the award posted.")
    if 0 < h["n"] < REGRET_MIN_LINES:
        out.append(f"{h['n']} compared line{'s' if h['n'] != 1 else ''} is too few to call: expect the sign of the increment "
                   f"to move until there are at least {REGRET_MIN_LINES}.")
    return out


def render_report(summary: dict, org_name: str, generated_at: date, inputs: dict | None = None,
                  cost_model: dict | None = None, contestability: dict | None = None, data_note: str = "") -> str:
    inputs = inputs or {}
    h, real, pol = summary["head_to_head"], summary["head_to_head_real_cost"], summary["policy"]
    hours = summary["hours_per_line"]

    def policy_rows(c: dict) -> list[str]:
        f, t = c["firm"], c["tool"]
        return [
            "| Policy | Bids | Wins | Win rate | Completed profit | Hours | Profit / hour | Regret |",
            "|---|---|---|---|---|---|---|---|",
            f"| Your process | {f['bids']} | {f['wins']} | {_pct(f['win_rate'])} | {_money(f['profit'])} | {f['hours']:.1f} | "
            f"{_money(f['profit_per_hour'])} | {_money(f['regret'])} |",
            f"| The tool | {t['bids']} (+{t['no_bids']} no-bids) | {t['wins']} | {_pct(t['win_rate'])} | {_money(t['profit'])} | "
            f"{t['hours']:.1f} | {_money(t['profit_per_hour'])} | {_money(t['regret'])} |",
            f"| Hindsight (best bid per line) | | | | {_money(c['oracle_profit'])} | | | $0 |",
        ]

    lines = [
        f"# Pilot: {org_name}",
        "",
        f"_Generated {generated_at.isoformat()}. {data_note}_".rstrip(),
        "",
        "Does following the tool make this broker more money per hour than its own process? Every line below was awarded, "
        "and the tool's decision (a bid or a no-bid) was recorded before the award posted. The firm's bids, purchase "
        "costs and outcomes come from its own records (imported) and from DIBBS award data.",
        "",
        "## Head to head: lines you bid on",
        "",
        f"{h['n']} awarded lines you bid on where the tool had decided beforehand; {h['n_real_cost']} with the purchase "
        f"cost you actually paid, {h['n_estimated_cost']} at the estimated landed cost. Pursuit effort: {hours:.2f} h per bid.",
        "",
        *policy_rows(h),
        "",
        f"**Increment: {_money(h['increment'])} of completed profit, {_money(h['increment_per_firm_hour'])} per hour you spent.**",
        "",
    ]
    if real["n"]:
        lines += ["### Real costs only", "", f"{real['n']} lines where you recorded what you paid.", "", *policy_rows(real),
                  "", f"Increment: {_money(real['increment'])} ({_money(real['increment_per_firm_hour'])} per hour).", ""]
    lines += [
        "## The tool as a policy over every line it decided",
        "",
        f"{pol['n']} awarded lines with a pre-award decision, including {pol['n'] - h['n']} you did not bid on (the tool's "
        "bids there are counterfactual wins or losses against the posted winning price).",
        "",
        *policy_rows(pol),
        "",
    ]
    for title, key in (("By supply class", "by_fsc"), ("By confidence tier", "by_confidence")):
        rows = summary.get(key) or []
        if not rows:
            continue
        lines += [f"## {title}", "", "| " + ("FSC" if key == "by_fsc" else "Confidence") +
                  " | Lines | Your wins | Your profit | Tool bids | Tool wins | Tool profit | Increment | Per hour |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for b in rows:
            lines.append(f"| {b['key']} | {b['n']} | {b['firm_wins']} | {_money(b['firm_profit'])} | {b['tool_bids']} | "
                         f"{b['tool_wins']} | {_money(b['tool_profit'])} | {_money(b['increment'])} | "
                         f"{_money(b['increment_per_firm_hour'])} |")
        lines.append("")
    if cost_model:
        tiers = cost_model.get("tiers") or {}
        lines += ["## Cost model: how well the tool guessed your supplier price before you asked", "",
                  f"Leave-one-out over your {cost_model.get('quotes', 0)} usable quotes (each estimated from your other quotes).", "",
                  "| Tier | n | Median abs. % error | Within 25% | 80% range coverage | Flat assumption error |",
                  "|---|---|---|---|---|---|"]
        for tier in ("nsn", "catalog", "cage", "fsc", "org", "prior", "all"):
            r = tiers.get(tier)
            if r:
                lines.append(f"| {tier} | {r['n']} | {_pct(r['mdape'])} | {_pct(r['within25'])} | {_pct(r['coverage_80'])} | "
                             f"{_pct(r['prior_mdape'])} |")
        if not tiers:
            lines.append("| (no quotes with a predicted price yet) | | | | | |")
        lines.append("")
    if contestability:
        lines += ["## Contestability: could a reseller win at all?", ""]
        own = contestability.get("own_lines") or {}
        if own.get("n"):
            lines += [f"On your {own['n']} awarded lines with a recorded P(winnable): Brier {own['brier']:.3f} "
                      f"(constant supply-class share: {own['baseline_brier']:.3f}; lower is better).", ""]
        rows = contestability.get("by_fsc") or []
        if rows:
            lines += ["| FSC | Awards scored | Reseller share | Brier (part history) | Brier (class share only) |",
                      "|---|---|---|---|---|"]
            for r in rows:
                if r.get("n"):
                    lines.append(f"| {r['fsc']} | {r['n']} | {_pct(r['reseller_share'])} | {r['brier']:.3f} | {r['baseline_brier']:.3f} |")
            lines.append("")
    lines += ["## Caveats", ""] + [f"- {c}" for c in caveats(summary, inputs)] + [""]
    if inputs:
        lines += ["## Inputs", "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in inputs.items()] + [""]
    scored = [s for s in summary.get("scored", []) if s["line"]["tool_decided"] and s["line"]["actual_bid"] is not None]
    if scored:
        lines += ["## Lines", "",
                  "| Solicitation | NSN | Qty | Winning price | Your bid | Won | Tool bid | Tool wins | Landed (basis) | Your profit | Tool profit | Increment |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in sorted(scored, key=lambda s: (s["line"]["award_date"] or "", s["line"]["solicitation_number"])):
            ln = s["line"]
            tool_bid = "no bid" if ln["rec_bid"] is None else f"${ln['rec_bid']:,.4g}"
            lines.append(f"| {ln['solicitation_number']} | {ln['nsn']} | {ln['quantity']:g} | ${ln['winning_price']:,.4g} | "
                         f"${ln['actual_bid']:,.4g} | {'yes' if s['firm_won'] else 'no'} | {tool_bid} | "
                         f"{'yes' if s['tool_wins'] else 'no'} | ${ln['landed']:,.4g} ({ln['cost_basis']}) | "
                         f"{_money(s['firm_profit'])} | {_money(s['tool_profit'])} | {_money(s['increment'])} |")
        lines.append("")
    return "\n".join(lines)
