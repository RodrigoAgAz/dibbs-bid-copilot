"""Competitor research harness (not used in production): does a part's award history say who can win it?

For every supply class in DATABASE_URL, walks the awards in time order and predicts, before each award, the chance
that a RESELLER (not an approved-source manufacturer) wins it: the part's reseller share so far, shrunk toward the
supply class's share (dibbs_ml.competitors). Reports the Brier score against the constant supply-class share, with
calibration bins, and lists the top winners per class with how far below the previous award they typically win.

    DATABASE_URL=... python ml/research/competitor_lab.py [--fsc 5305,5310]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ml")]

from dibbs_ml.competitors import evaluate_contestability  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fsc", default=None, help="comma-separated supply classes (default: every one with awards)")
    args = ap.parse_args()
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import AwardHistory
    from app.services.competitors import build_fsc_competitors, load_fsc_awards, manufacturer_cages

    with session_scope() as db:
        if args.fsc:
            fscs = [f.strip() for f in args.fsc.split(",") if f.strip()]
        else:
            fscs = [f for (f,) in db.execute(select(func.substr(AwardHistory.nsn, 1, 4)).distinct()).all()]
        oem = manufacturer_cages(db)
        print(f"manufacturer CAGEs known: {len(oem)}")
        print("\n| FSC | Awards scored | Reseller share | Brier (part history) | Brier (FSC share only) |")
        print("|---|---|---|---|---|")
        for fsc in sorted(fscs):
            df = load_fsc_awards(db, fsc)
            if df.empty:
                continue
            res = evaluate_contestability(df, oem, fsc)
            if not res.get("n"):
                continue
            print(f"| {fsc} | {res['n']:,} | {100 * res['reseller_share']:.0f}% | {res['brier']:.3f} | {res['baseline_brier']:.3f} |")
        print("\nTop winners per supply class (wins; typical price vs the inflation-adjusted previous award):")
        for fsc in sorted(fscs):
            comp = build_fsc_competitors(db, fsc)
            if not comp.profiles:
                continue
            tops = comp.top_players(6)
            desc = ", ".join(
                f"{p.name or p.cage} ({p.wins}{' OEM' if p.is_manufacturer else ''}"
                + (f", {100 * (p.median_ratio - 1):+.0f}%" if p.median_ratio else "") + ")"
                for p in tops
            )
            print(f"- {fsc}: resellers {100 * comp.reseller_share:.0f}% of awards; {desc}")


if __name__ == "__main__":
    main()
