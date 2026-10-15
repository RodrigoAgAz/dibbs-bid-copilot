"""Coverage harness (not used in production): before integrating any distributor feed or buying FLIS data, measure
how much of an organization's open work they would actually touch.

For one organization in DATABASE_URL:
  * catalog coverage: share of its open lines (watched FSCs) whose approved part numbers match an imported catalog row,
    by FSC (import price lists first: python -m app.cli import-catalog ...);
  * characteristics coverage: share of open-line NSNs with FLIS / PUB LOG characteristics loaded, by FSC;
  * sourcing coverage: share of open lines with at least one candidate supplier that has an email address.

    DATABASE_URL=... python ml/research/coverage_lab.py --org 1
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ml")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", type=int, required=True)
    args = ap.parse_args()
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import ItemCharacteristic, Organization, OrgSettings
    from app.services.cost_model import catalog_prices_for, tenant_catalog
    from app.services.recommendations import open_lines
    from app.services.sourcing import candidate_suppliers

    with session_scope() as db:
        org = db.get(Organization, args.org)
        s = db.get(OrgSettings, args.org)
        if org is None or s is None:
            names = ", ".join(f"{o.id}: {o.name}" for o in db.execute(select(Organization)).scalars())
            sys.exit(f"no organization {args.org} (have {names})")
        lines = open_lines(db, fscs=list(s.fsc_watchlist or []))
        catalog = tenant_catalog(db, args.org)
        nsns = {ln.nsn for ln in lines}
        with_chars = {n for (n,) in db.execute(select(ItemCharacteristic.nsn).where(ItemCharacteristic.nsn.in_(nsns))).all()} if nsns else set()
        by_fsc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ln in lines:
            c = by_fsc[ln.fsc]
            c["lines"] += 1
            cands = candidate_suppliers(db, args.org, ln)
            approved = [(x.cage_code, pn) for x in cands if x.reason == "approved_source" for pn in x.part_numbers]
            c["approved_parts"] += int(bool(approved))
            c["catalog_match"] += int(bool(catalog_prices_for(catalog, approved))) if catalog else 0
            c["characteristics"] += int(ln.nsn in with_chars)
            c["candidate_with_email"] += int(any(x.contact and x.contact.email for x in cands))
        print(f"organization {args.org}: {len(lines)} open lines, {len(catalog) // 2 if catalog else 0} catalog part numbers, "
              f"{len(with_chars)} NSNs with characteristics")
        print("\n| FSC | Open lines | With approved P/N | Catalog match | Characteristics | A candidate with email |")
        print("|---|---|---|---|---|---|")
        for fsc in sorted(by_fsc):
            c = by_fsc[fsc]
            n = c["lines"] or 1
            print(f"| {fsc} | {c['lines']} | {100 * c['approved_parts'] / n:.0f}% | {100 * c['catalog_match'] / n:.0f}% | "
                  f"{100 * c['characteristics'] / n:.0f}% | {100 * c['candidate_with_email'] / n:.0f}% |")


if __name__ == "__main__":
    main()
