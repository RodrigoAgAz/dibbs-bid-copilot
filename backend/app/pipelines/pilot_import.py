"""Pilot imports: a firm's own bids, purchase costs and outcomes, mapped onto the tool's rows.

The pilot loop (DECISIONS.md, step 7) needs what the firm actually did on real lines: what it bid, what it paid when it
won, and how the award went when DIBBS award labeling has not already recorded it. Three delimited text files (CSV or
TSV, header row, columns matched by name, case-insensitively, like the enrichment imports):

* bids:      solicitation number, NSN (or NIIN), bid per unit, date [, line no, quantity]
* costs:     solicitation number, NSN, cost per unit [, freight (total for the line) | freight per unit, date paid, quantity]
* outcomes:  solicitation number, NSN, outcome (won/lost) or awardee CAGE [, award price, award date, contract, quantity]

Mapping. A bid or cost lands on the organization's `recommendations` row for that solicitation line (`actual_bid`,
`actual_cost_per_unit`) with an append-only `recommendation_snapshots` row (kind actual_bid / actual_cost) dated when
the firm acted, so the evaluation can tell what the tool had shown before the award. A line the tool never priced gets
a "historical" recommendation (source = "historical": no bid, no price, excluded from the dashboard), and a solicitation
the tool never ingested gets a stub solicitation + line (public tables; closed on the bid date) so the row has a home.
Outcomes become `award_history` rows with source "pilot_import" only where no award row exists yet; the firm's own
outcome is trusted for its pilot evaluation, and is treated like a derived price everywhere else (data_quality.py).

Idempotent: re-importing the same file changes nothing and adds no snapshots.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AwardHistory,
    NsnItem,
    Organization,
    Recommendation,
    RecommendationSnapshot,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.enrichment import _num, _pick, _rows, read_text  # noqa: F401  (read_text re-exported for the CLI)
from app.services.recommendations import record_snapshot

log = logging.getLogger(__name__)

PILOT_SOURCE = "pilot_import"
HISTORICAL = "historical"

SOL_COLUMNS = ("solicitation", "solicitation number", "solicitation_number", "solicitation no", "sol", "sol no", "sol #",
               "rfq", "rfq number", "rfq #", "rfq no", "solicitation #")
NSN_COLUMNS = ("nsn", "niin", "national stock number", "stock number")
QTY_COLUMNS = ("quantity", "qty", "quantity bid", "quantity purchased", "quantity awarded", "award quantity")
LINE_COLUMNS = ("line", "line no", "line_no", "line number", "clin", "item")
BID_COLUMNS = {
    "bid": ("bid", "bid per unit", "unit bid", "bid price", "our bid", "bid_per_unit", "quoted price", "unit price", "price"),
    "date": ("bid date", "date", "submitted", "date submitted", "quote date", "bid_date", "date bid"),
}
COST_COLUMNS = {
    "cost": ("cost per unit", "unit cost", "cost", "actual cost", "purchase price", "po price", "po unit price",
             "supplier price", "actual_cost_per_unit", "paid per unit"),
    "freight": ("freight", "freight cost", "freight total", "shipping", "shipping cost"),
    "freight_per_unit": ("freight per unit", "freight_per_unit", "shipping per unit"),
    "date": ("date paid", "paid", "paid on", "date", "invoice date", "po date"),
}
OUTCOME_COLUMNS = {
    "outcome": ("outcome", "result", "status", "won", "win", "won?"),
    "award_price": ("award price", "winning price", "award unit price", "awarded price", "winning_price", "unit price", "price"),
    "award_date": ("award date", "awarded", "awarded on", "date awarded", "award_date", "date"),
    "awardee": ("awardee", "awardee cage", "winner", "winner cage", "awarded to", "cage"),
    "contract": ("contract", "contract number", "contract_number", "award number", "po number", "award"),
}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y", "%Y%m%d",
                "%d.%m.%Y")
WON_WORDS = {"won", "win", "w", "yes", "y", "true", "1", "awarded to us", "us", "winner"}
LOST_WORDS = {"lost", "lose", "loss", "l", "no", "n", "false", "0", "awarded", "other", "awarded to other", "not awarded"}
MAX_ERRORS = 25


def parse_date(value: str | None) -> date | None:
    s = (value or "").strip()
    if not s:
        return None
    if "T" in s and len(s) >= 10:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm_solicitation(value: str | None) -> str:
    """DIBBS solicitation numbers are shown as SPE4A6-27-T-0486 and stored as SPE4A627T0486."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _at(d: date) -> datetime:
    """Snapshot timestamp for an action the firm took on a given day (midday UTC: before any award of a later day)."""
    return datetime.combine(d, time(12, 0), tzinfo=UTC)


class _Table:
    """A parsed file with the columns the import needs."""

    def __init__(self, text: str, extra: dict[str, tuple[str, ...]]) -> None:
        self.headers, self.rows = _rows(text)
        self.idx: dict[str, int | None] = {}
        if self.headers:
            self.idx = {"solicitation": _pick(self.headers, SOL_COLUMNS), "nsn": _pick(self.headers, NSN_COLUMNS),
                        "quantity": _pick(self.headers, QTY_COLUMNS), "line": _pick(self.headers, LINE_COLUMNS)}
            for key, names in extra.items():
                # A generic header ("price", "date") must not steal a column already claimed by a specific one.
                taken = {i for i in self.idx.values() if i is not None}
                self.idx[key] = _pick([h if i not in taken else "" for i, h in enumerate(self.headers)], names)

    def col(self, row: list[str], key: str) -> str:
        i = self.idx.get(key)
        return row[i].strip() if (i is not None and i < len(row)) else ""


def _find_line(db: Session, sol_number: str, nsn_or_niin: str, line_no: int | None) -> tuple[Solicitation | None, SolicitationLine | None]:
    sol = db.execute(select(Solicitation).where(Solicitation.solicitation_number == sol_number)).scalar_one_or_none()
    if sol is None:
        return None, None
    lines = list(sol.lines)
    digits = re.sub(r"\D", "", nsn_or_niin or "")
    if digits:
        for ln in lines:
            if (len(digits) == 13 and ln.nsn == digits) or (len(digits) == 9 and ln.niin == digits):
                return sol, ln
    if line_no is not None:
        for ln in lines:
            if ln.line_no == line_no:
                return sol, ln
    if not digits and line_no is None and len(lines) == 1:
        return sol, lines[0]
    return sol, None


def _stub_line(db: Session, sol_number: str, nsn: str, quantity: float, closed_on: date, line_no: int | None,
               sol: Solicitation | None) -> SolicitationLine:
    """A solicitation line the tool never ingested, so the firm's bid has a row to live on. Closed on the bid date, so
    it never appears as an open opportunity."""
    if sol is None:
        sol = Solicitation(solicitation_number=sol_number, type_code=sol_number[8] if len(sol_number) > 8 else None,
                           return_by_date=closed_on, raw_json={"source": PILOT_SOURCE})
        db.add(sol)
        db.flush()
    item = db.get(NsnItem, nsn)
    line = SolicitationLine(solicitation_id=sol.id, line_no=line_no or (max((ln.line_no for ln in sol.lines), default=0) + 1),
                            nsn=nsn, fsc=nsn[:4], niin=nsn[4:], quantity=float(quantity or 1.0),
                            nomenclature=item.nomenclature if item else None, unit_of_issue=item.unit_of_issue if item else None)
    db.add(line)
    db.flush()
    return line


def _recommendation(db: Session, org_id: int, line: SolicitationLine, source_file: str | None) -> tuple[Recommendation, bool]:
    rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                  Recommendation.solicitation_line_id == line.id)).scalar_one_or_none()
    if rec is not None:
        return rec, False
    rec = Recommendation(organization_id=org_id, solicitation_line_id=line.id, source=HISTORICAL, provisional=True,
                         no_bid=False, explanation={"historical": True, "source_file": source_file,
                                                    "note": "imported from the firm's records; the tool never priced this line"})
    db.add(rec)
    db.flush()
    return rec, True


def _has_snapshot(db: Session, rec: Recommendation, kind: str, value: float, at: datetime) -> bool:
    key = "actual_bid" if kind == "actual_bid" else "actual_cost_per_unit"
    for s in db.execute(select(RecommendationSnapshot).where(RecommendationSnapshot.recommendation_id == rec.id,
                                                             RecommendationSnapshot.kind == kind)).scalars():
        v = (s.payload or {}).get(key)
        if v is not None and abs(float(v) - value) < 1e-6 and s.created_at is not None and s.created_at.date() == at.date():
            return True
    return False


def _result(table: _Table, **counts) -> dict:
    return {"rows": len(table.rows), **counts}


# --------------------------------------------------------------------------------------------------------- bids
def import_bids(db: Session, org_id: int, text: str, source_file: str | None = None) -> dict:
    """The firm's historical bids. Returns counts (matched: onto an engine recommendation; historical: new rows for
    lines the tool never priced; created_lines: stub solicitation lines for solicitations never ingested)."""
    t = _Table(text, BID_COLUMNS)
    if not t.headers:
        return {"rows": 0, "error": "empty file"}
    if t.idx["solicitation"] is None or t.idx["bid"] is None:
        return _result(t, loaded=0, skipped=len(t.rows), error=f"need solicitation number and bid columns; got {t.headers}")
    matched = historical = created_lines = unchanged = skipped = 0
    errors: list[str] = []
    for n, r in enumerate(t.rows, start=2):
        sol_number = norm_solicitation(t.col(r, "solicitation"))
        bid = _num(t.col(r, "bid"))
        when = parse_date(t.col(r, "date"))
        nsn_raw = t.col(r, "nsn")
        line_no = int(_num(t.col(r, "line")) or 0) or None
        qty = _num(t.col(r, "quantity"))
        if not sol_number or bid is None or bid <= 0:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: need a solicitation number and a positive bid")
            continue
        if when is None:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: unreadable or missing bid date")
            continue
        sol, line = _find_line(db, sol_number, nsn_raw, line_no)
        if line is None:
            digits = re.sub(r"\D", "", nsn_raw)
            if len(digits) != 13:
                skipped += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(f"row {n}: {sol_number} is not in the database and the row has no 13-digit NSN to create it")
                continue
            line = _stub_line(db, sol_number, digits, qty or 1.0, when, line_no, sol)
            created_lines += 1
        rec, created = _recommendation(db, org_id, line, source_file)
        at = _at(when)
        value = round(bid, 4)
        if rec.actual_bid is not None and abs(rec.actual_bid - value) < 1e-6 and _has_snapshot(db, rec, "actual_bid", value, at):
            unchanged += 1
            continue
        rec.actual_bid = value
        rec.actual_bid_at = at
        snap = record_snapshot(db, rec, "actual_bid", imported=True, source_file=source_file, bid_date=when.isoformat(),
                               quantity_bid=qty)
        snap.created_at = at
        if created:
            historical += 1
        else:
            matched += 1
    db.flush()
    return _result(t, matched=matched, historical=historical, created_lines=created_lines, unchanged=unchanged,
                   skipped=skipped, errors=errors)


# -------------------------------------------------------------------------------------------------------- costs
def import_costs(db: Session, org_id: int, text: str, source_file: str | None = None) -> dict:
    """What the firm paid per unit on won lines, landed: cost per unit + freight per unit (a freight total is divided
    by the quantity purchased, else the line's quantity)."""
    t = _Table(text, COST_COLUMNS)
    if not t.headers:
        return {"rows": 0, "error": "empty file"}
    if t.idx["solicitation"] is None or t.idx["cost"] is None:
        return _result(t, loaded=0, skipped=len(t.rows), error=f"need solicitation number and cost per unit columns; got {t.headers}")
    loaded = historical = unchanged = skipped = 0
    errors: list[str] = []
    for n, r in enumerate(t.rows, start=2):
        sol_number = norm_solicitation(t.col(r, "solicitation"))
        cost = _num(t.col(r, "cost"))
        when = parse_date(t.col(r, "date"))
        line_no = int(_num(t.col(r, "line")) or 0) or None
        if not sol_number or cost is None or cost <= 0:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: need a solicitation number and a positive cost per unit")
            continue
        sol, line = _find_line(db, sol_number, t.col(r, "nsn"), line_no)
        if line is None:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: {sol_number} / {t.col(r, 'nsn') or 'no NSN'} is not in the database (import the bids first)")
            continue
        qty = _num(t.col(r, "quantity")) or float(line.quantity or 1.0)
        freight_total = _num(t.col(r, "freight")) or 0.0
        freight_unit = _num(t.col(r, "freight_per_unit")) or 0.0
        landed = round(cost + freight_unit + (freight_total / qty if qty > 0 else 0.0), 4)
        rec, created = _recommendation(db, org_id, line, source_file)
        at = _at(when) if when else datetime.now(UTC)
        if rec.actual_cost_per_unit is not None and abs(rec.actual_cost_per_unit - landed) < 1e-6 and \
                _has_snapshot(db, rec, "actual_cost", landed, at):
            unchanged += 1
            continue
        rec.actual_cost_per_unit = landed
        rec.actual_cost_at = at
        snap = record_snapshot(db, rec, "actual_cost", imported=True, source_file=source_file, unit_cost=cost,
                               freight_total=freight_total, freight_per_unit=freight_unit, quantity=qty,
                               paid_date=when.isoformat() if when else None)
        snap.created_at = at
        loaded += 1
        historical += int(created)
    db.flush()
    return _result(t, loaded=loaded, historical=historical, unchanged=unchanged, skipped=skipped, errors=errors)


# ----------------------------------------------------------------------------------------------------- outcomes
def _won(outcome: str, awardee: str, org_cage: str) -> bool | None:
    if awardee:
        return awardee.upper() == org_cage
    o = outcome.strip().lower()
    if o in WON_WORDS:
        return True
    if o in LOST_WORDS:
        return False
    return None


def import_outcomes(db: Session, org_id: int, text: str, source_file: str | None = None) -> dict:
    """Outcomes the firm knows that award labeling has not recorded: one award_history row per line (source
    pilot_import), skipped wherever an award row already exists."""
    t = _Table(text, OUTCOME_COLUMNS)
    if not t.headers:
        return {"rows": 0, "error": "empty file"}
    if t.idx["solicitation"] is None or (t.idx["outcome"] is None and t.idx["awardee"] is None):
        return _result(t, loaded=0, skipped=len(t.rows), error=f"need solicitation number and outcome (or awardee CAGE) columns; got {t.headers}")
    org = db.get(Organization, org_id)
    cage = (org.cage_code or "").strip().upper() if org else ""
    if not cage:
        return _result(t, loaded=0, skipped=len(t.rows),
                       error="set the organization's CAGE code first: won lines are recognised by the awardee CAGE")
    loaded = already = skipped = 0
    errors: list[str] = []
    for n, r in enumerate(t.rows, start=2):
        sol_number = norm_solicitation(t.col(r, "solicitation"))
        sol, line = _find_line(db, sol_number, t.col(r, "nsn"), int(_num(t.col(r, "line")) or 0) or None)
        if line is None:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: {sol_number or '(blank)'} / {t.col(r, 'nsn') or 'no NSN'} is not in the database (import the bids first)")
            continue
        existing = db.execute(select(AwardHistory.id).where(AwardHistory.solicitation_number == sol_number,
                                                            AwardHistory.nsn == line.nsn).limit(1)).first()
        if existing is not None:
            already += 1
            continue
        awardee = t.col(r, "awardee").upper()[:5]
        won = _won(t.col(r, "outcome"), awardee, cage)
        if won is None:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: outcome {t.col(r, 'outcome')!r} is neither won nor lost and no awardee CAGE is given")
            continue
        rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                      Recommendation.solicitation_line_id == line.id)).scalar_one_or_none()
        price = _num(t.col(r, "award_price"))
        if (price is None or price <= 0) and won and rec is not None and rec.actual_bid:
            price = rec.actual_bid  # the firm won: the award price is its own bid
        when = parse_date(t.col(r, "award_date")) or (sol.return_by_date if sol is not None else None)
        if price is None or price <= 0 or when is None:
            skipped += 1
            if len(errors) < MAX_ERRORS:
                errors.append(f"row {n}: {sol_number} needs a winning price and an award date"
                              + (" (a won line without a recorded bid has no price)" if won else ""))
            continue
        qty = _num(t.col(r, "quantity")) or float(line.quantity or 1.0)
        db.add(AwardHistory(
            nsn=line.nsn, award_date=when, contract_number=(t.col(r, "contract") or f"PILOT-{sol_number}")[:40],
            awardee_cage=(awardee or (cage if won else None)), quantity=qty, unit_of_issue=line.unit_of_issue,
            unit_price=round(price, 4), total_value=round(price * qty, 2), solicitation_number=sol_number, source=PILOT_SOURCE,
        ))
        loaded += 1
    db.flush()
    return _result(t, loaded=loaded, already_labeled=already, skipped=skipped, errors=errors)


def import_pilot_files(db: Session, org_id: int, bids: str | Path | None = None, costs: str | Path | None = None,
                       outcomes: str | Path | None = None) -> dict:
    """Bids, then costs, then outcomes (each optional), from file paths."""
    out: dict = {}
    if bids:
        out["bids"] = import_bids(db, org_id, read_text(bids), source_file=Path(bids).name)
    if costs:
        out["costs"] = import_costs(db, org_id, read_text(costs), source_file=Path(costs).name)
    if outcomes:
        out["outcomes"] = import_outcomes(db, org_id, read_text(outcomes), source_file=Path(outcomes).name)
    return out
