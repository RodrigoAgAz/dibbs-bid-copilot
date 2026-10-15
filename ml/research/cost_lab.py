"""Cost research harness (not used in production): how predictable is a broker's supplier price before asking?

For every organization in DATABASE_URL, loads its usable supplier quotes the way the live cost model does
(app/services/cost_model.py), scores each quote from the organization's OTHER quotes (leave-one-out) and reports
median absolute % error and 80%-range coverage by tier (nsn / cage / fsc / org / prior) against the flat
"70% of the predicted price" assumption. Quotes from demo organizations are marked as such: they are simulated.

    DATABASE_URL=postgresql+psycopg://... python ml/research/cost_lab.py [--org ID]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ml")]

from dibbs_ml.cost_model import TenantCostModel, summarize_loo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", type=int, default=None, help="one organization id (default: all)")
    args = ap.parse_args()
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Organization, OrgSettings
    from app.services.cost_model import tenant_quote_observations

    with session_scope() as db:
        orgs = list(db.execute(select(Organization)).scalars())
        if args.org:
            orgs = [o for o in orgs if o.id == args.org]
        print("| Org | Plan | Quotes | Tier | n | MdAPE | within 25% | 80% coverage | flat 70% MdAPE |")
        print("|---|---|---|---|---|---|---|---|---|")
        for org in orgs:
            s = db.get(OrgSettings, org.id)
            obs = tenant_quote_observations(db, org.id)
            if not obs:
                continue
            model = TenantCostModel(obs, prior_fraction=s.provisional_cost_fraction if s else 0.7)
            summary = summarize_loo(model.leave_one_out())
            for tier in ("nsn", "cage", "fsc", "org", "prior", "all"):
                r = summary.get(tier)
                if not r:
                    continue
                print(f"| {org.name} | {org.plan} | {len(obs)} | {tier} | {r['n']} | {100 * r['mdape']:.0f}% | "
                      f"{100 * r['within25']:.0f}% | {100 * r['coverage_80']:.0f}% | {100 * r['prior_mdape']:.0f}% |")


if __name__ == "__main__":
    main()
