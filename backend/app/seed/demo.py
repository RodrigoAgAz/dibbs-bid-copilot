"""`make seed`: a complete, offline demo.

Creates (idempotently; re-running resets the demo data):
  * two demo organizations with one owner each + a platform superadmin -> DEMO_CREDENTIALS.md
  * history for ~300 NSNs across the default FSCs: synthetic (SPEZZ* solicitation numbers, fictional
    companies) unless >= 500 real award rows already exist from a backfill
  * 40 open "today" RFQ lines, a trained model, predictions, recommendations, quote requests,
    parsed mock supplier quotes (some needing review), provisional and no-bid lines
  * a few past lines with actual bids and awards so /history has outcomes
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select

from app.config import REPO_ROOT, get_settings
from app.core.security import hash_password
from app.core.timeutil import today_et
from app.db import session_scope
from app.models import (
    ApprovedSource,
    AwardHistory,
    Company,
    ModelRun,
    NsnItem,
    Organization,
    OrgSettings,
    PhoneType,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierContact,
    User,
    UserRole,
)
from dibbs_ml.synthetic import SYNTHETIC_PREFIX, generate

log = logging.getLogger(__name__)

DEMO_ORGS = [
    {"name": "Atlas Federal Supply (demo)", "cage": "9ZDM1", "email": "owner@atlas-demo.example.com",
     "password": "AtlasDemo!2026", "full_name": "Avery Atlas"},
    {"name": "Bluewater Parts Co. (demo)", "cage": "9ZDM2", "email": "owner@bluewater-demo.example.com",
     "password": "BluewaterDemo!2026", "full_name": "Blair Bluewater"},
]
SUPERADMIN = {"email": "admin@copilot-demo.example.com", "password": "AdminDemo!2026"}
REAL_HISTORY_THRESHOLD = 500


def _say(msg: str) -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------------------- reset + history
def _reset_demo(db) -> None:
    demo_sols = select(Solicitation.id).where(Solicitation.solicitation_number.like(f"{SYNTHETIC_PREFIX}%"))
    db.execute(delete(Solicitation).where(Solicitation.id.in_(demo_sols)))
    db.execute(delete(AwardHistory).where(AwardHistory.source == "synthetic"))
    db.execute(delete(ApprovedSource).where(ApprovedSource.source == "synthetic"))
    db.execute(delete(Company).where(Company.source == "synthetic"))
    # Core DELETE so the database's ON DELETE CASCADE removes users, settings, quotes, recommendations...
    db.execute(delete(Organization).where(Organization.name.in_([o["name"] for o in DEMO_ORGS])))
    db.flush()


def _insert_synthetic(db, data) -> None:
    db.execute(
        insert(Company),
        [
            {"cage_code": c["cage_code"], "name": c["name"], "city": c["city"], "state": c["state"],
             "country": c["country"], "is_manufacturer_guess": bool(c["is_manufacturer_guess"]), "source": "synthetic"}
            for c in data.companies.to_dict("records")
            if db.get(Company, c["cage_code"]) is None
        ],
    )
    existing_nsn = {n for (n,) in db.execute(select(NsnItem.nsn)).all()}
    rows = [
        {"nsn": r["nsn"], "fsc": r["fsc"], "nomenclature": r["nomenclature"], "unit_of_issue": r["unit_of_issue"],
         "amsc": r["amsc"]}
        for r in data.nsns.to_dict("records") if r["nsn"] not in existing_nsn
    ]
    if rows:
        db.execute(insert(NsnItem), rows)
    db.execute(
        insert(ApprovedSource),
        [{"nsn": r["nsn"], "cage_code": r["cage_code"], "part_number": r["part_number"], "source": "synthetic"}
         for r in data.approved_sources.to_dict("records")],
    )
    aw = data.awards
    db.execute(
        insert(AwardHistory),
        [
            {"nsn": r["nsn"], "award_date": r["award_date"], "contract_number": r["contract_number"],
             "awardee_cage": r["awardee_cage"], "quantity": r["quantity"], "unit_of_issue": r["unit_of_issue"],
             "unit_price": r["unit_price"], "total_value": r["total_value"],
             "solicitation_number": r["solicitation_number"], "source": "synthetic"}
            for r in aw.to_dict("records")
        ],
    )
    # Historical solicitations + lines so training rows get solicitation context.
    sols = aw.drop_duplicates("solicitation_number")
    db.execute(
        insert(Solicitation),
        [
            {"solicitation_number": r["solicitation_number"], "type_code": r["type_code"], "issue_date": r["issue_date"],
             "return_by_date": r["issue_date"] + timedelta(days=10), "buyer_code": r["buyer_code"],
             "set_aside": r["set_aside"], "fast_award": bool(r["fast_award"]), "aidc": bool(r["aidc"]),
             "inspection_point": r["inspection_point"], "fob": r["fob"], "first_article": bool(r["first_article"]),
             "status": "Awarded", "raw_json": {"synthetic": True}, "details_fetched_at": datetime.now(UTC)}
            for r in sols.to_dict("records")
        ],
    )
    ids = dict(db.execute(
        select(Solicitation.solicitation_number, Solicitation.id).where(Solicitation.solicitation_number.like(f"{SYNTHETIC_PREFIX}%"))
    ).all())
    nsn_meta = data.nsns.set_index("nsn")
    db.execute(
        insert(SolicitationLine),
        [
            {"solicitation_id": ids[r["solicitation_number"]], "line_no": 1, "nsn": r["nsn"], "fsc": r["fsc"],
             "niin": r["nsn"][4:], "nomenclature": nsn_meta.loc[r["nsn"], "nomenclature"], "quantity": r["quantity"],
             "unit_of_issue": r["unit_of_issue"], "dod_standard_price": float(r["dod_standard_price"]),
             "amsc": r["amsc"], "approved_source_only": bool(r["approved_source_only"]), "delivery_days": 90}
            for r in sols.to_dict("records")
        ],
    )
    db.flush()


def _insert_open_lines(db, data) -> list[SolicitationLine]:
    nsn_meta = data.nsns.set_index("nsn")
    lines = []
    for r in data.open_lines.to_dict("records"):
        sol = Solicitation(
            solicitation_number=r["solicitation_number"], type_code=r["type_code"], issue_date=r["issue_date"],
            return_by_date=r["return_by_date"], buyer_code=r["buyer_code"], buyer_name="Demo Buyer",
            buyer_email="buyer@dla-demo.example.com", set_aside=r["set_aside"], fast_award=r["fast_award"],
            aidc=r["aidc"], inspection_point=r["inspection_point"], fob=r["fob"], first_article=r["first_article"],
            naics="332722", status="Open",
            packaging="Military packaging per MIL-STD-2073-1E; marking per MIL-STD-129" if random.random() < 0.6
            else "Commercial packaging per ASTM D3951; marking per MIL-STD-129",
            raw_json={"synthetic": True, "true_next_price": round(r["_true_next_price"], 4)},
            details_fetched_at=datetime.now(UTC),
        )
        db.add(sol)
        db.flush()
        line = SolicitationLine(
            solicitation_id=sol.id, line_no=1, nsn=r["nsn"], fsc=r["fsc"], niin=r["niin"],
            nomenclature=r["nomenclature"], quantity=r["quantity"], unit_of_issue=r["unit_of_issue"],
            dod_standard_price=float(nsn_meta.loc[r["nsn"], "dod_standard_price"]), amsc=r["amsc"],
            approved_source_only=r["approved_source_only"], delivery_days=r["delivery_days"],
            purchase_request=f"70{random.randint(10**7, 10**8 - 1)}",
        )
        db.add(line)
        lines.append(line)
    db.flush()
    return lines


# ----------------------------------------------------------------------------- orgs & suppliers
def _upsert_user(db, org: Organization, email: str, password: str, role: UserRole, full_name: str | None,
                 superadmin: bool = False) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(organization_id=org.id, email=email)
        db.add(user)
    user.organization_id = org.id
    user.password_hash = hash_password(password)
    user.role = role
    user.full_name = full_name
    user.is_superadmin = superadmin
    user.is_active = True
    return user


def _create_orgs(db) -> list[Organization]:
    orgs = []
    for o in DEMO_ORGS:
        org = Organization(name=o["name"], cage_code=o["cage"], plan="demo")
        db.add(org)
        db.flush()
        db.add(OrgSettings(organization_id=org.id))
        _upsert_user(db, org, o["email"], o["password"], UserRole.owner, o["full_name"])
        orgs.append(org)
    admin_org = db.execute(select(Organization).where(Organization.name == "Platform Admin")).scalar_one_or_none()
    if admin_org is None:
        admin_org = Organization(name="Platform Admin", plan="internal")
        db.add(admin_org)
        db.flush()
        db.add(OrgSettings(organization_id=admin_org.id))
    _upsert_user(db, admin_org, SUPERADMIN["email"], SUPERADMIN["password"], UserRole.owner, "Platform Admin", True)
    db.get(OrgSettings, orgs[0].id).set_aside_eligibility = ["SB", "WOSB"]
    # Second org watches a narrower niche, so the two dashboards differ.
    db.get(OrgSettings, orgs[1].id).fsc_watchlist = ["5305", "5306", "5310", "5330"]
    db.flush()
    from app.services.accounts import ensure_demo_viewer

    ensure_demo_viewer(db)  # read-only account behind "Explore the demo" (PUBLIC_DEMO=true)
    db.get(OrgSettings, orgs[1].id).min_margin_pct = 0.15
    db.flush()
    return orgs


def _create_contacts(db, org: Organization, lines: list[SolicitationLine], rng: random.Random) -> None:
    cages: list[str] = []
    for ln in lines:
        cages += [a.cage_code for a in db.execute(select(ApprovedSource).where(ApprovedSource.nsn == ln.nsn)).scalars()]
        cages += [c for (c,) in db.execute(
            select(AwardHistory.awardee_cage).where(AwardHistory.nsn == ln.nsn).group_by(AwardHistory.awardee_cage)
            .order_by(func.count().desc()).limit(2)
        ).all() if c]
    seen = []
    for c in cages:
        if c not in seen:
            seen.append(c)
    first_names = ["Dana", "Jordan", "Morgan", "Casey", "Riley", "Taylor", "Jamie", "Quinn", "Reese", "Skyler"]
    for i, cage in enumerate(seen[:45]):
        comp = db.get(Company, cage)
        slug = "".join(ch for ch in (comp.name if comp and comp.name else cage).lower() if ch.isalnum())[:18]
        ptype = rng.choice([PhoneType.landline, PhoneType.landline, PhoneType.mobile, PhoneType.unknown])
        db.add(
            SupplierContact(
                organization_id=org.id, cage_code=cage, company_name=comp.name if comp else None,
                name=f"{rng.choice(first_names)} {rng.choice(['Lee', 'Patel', 'Garcia', 'Nguyen', 'Smith', 'Kim'])}",
                email=f"quotes@{slug}.example.com" if i % 9 != 8 else None,
                phone=f"+1555{rng.randint(1000000, 9999999)}", phone_type=ptype,
                call_consent=ptype == PhoneType.mobile and rng.random() < 0.5,
                fsc_tags=[lines[0].fsc] if i % 7 == 0 else [],
                notes="Synthetic demo supplier",
            )
        )
    db.flush()


# ----------------------------------------------------------------------------- mock replies
def _reply_text(kind: str, pn: str | None, price: float, rng: random.Random) -> str:
    pn = pn or "per NSN"
    weeks = rng.randint(1, 8)
    if kind == "good":
        return (f"Hi,\n\nThanks for the RFQ. We can supply P/N {pn}.\nUnit price: ${price:.2f} each, lead time "
                f"{weeks} weeks ARO. Quote valid for 30 days. Freight: prepaid and add. CoC included with shipment."
                "\n\nBest regards,\nSales")
    if kind == "detailed":
        return (f"Quote for P/N {pn}: ${price:.2f}/ea, MOQ {rng.randint(1, 25)}, delivery {rng.randint(5, 20)} business "
                f"days. FOB origin, shipping ${rng.randint(15, 60)}.00. Certificate of conformance and full "
                "traceability available.")
    if kind == "ambiguous":
        return (f"We have these in stock. Pricing is about ${price * 1.1:.2f} for small lots or ${price * 0.92:.2f} "
                "for larger quantities. Let me know what works.")
    if kind == "outlier":
        return f"Unit price: ${price * 8:.2f} each for P/N {pn}, lead time 16 weeks ARO."
    if kind == "alternate":
        return f"We can't get the exact part but can offer an equivalent, P/N ALT-{rng.randint(1000, 9999)}, unit price ${price * 0.8:.2f} each, 3 weeks."
    return "Sorry, we are unable to quote this item at this time."


def _simulate_quotes(db, org: Organization, lines: list[SolicitationLine], rng: random.Random, share: float) -> int:
    from app.services.sourcing import process_inbound_email, send_quote_request

    n = 0
    for ln in lines:
        if rng.random() > share:
            continue
        qrs = db.execute(
            select(QuoteRequest).where(QuoteRequest.organization_id == org.id, QuoteRequest.solicitation_line_id == ln.id)
        ).scalars().all()
        true_price = float((ln.solicitation.raw_json or {}).get("true_next_price") or 10)
        for qr in qrs[:3]:
            if not qr.email_to:
                continue
            send_quote_request(db, qr)
            qr.sent_at = datetime.now(UTC) - timedelta(hours=rng.randint(3, 60))
            if rng.random() < 0.25:
                continue  # still waiting for a reply
            kind = rng.choices(["good", "detailed", "ambiguous", "outlier", "alternate", "decline"],
                               weights=[40, 25, 8, 4, 8 if ln.approved_source_only else 0, 10])[0]
            price = true_price * rng.uniform(0.55, 0.9)
            process_inbound_email(db, {"to": f"quotes+{qr.reply_token}@{get_settings().email_reply_domain}",
                                       "from": qr.email_to, "subject": f"RE: {qr.email_subject}",
                                       "text": _reply_text(kind, qr.part_number, price, rng)})
            n += 1
    db.flush()
    return n


def _past_outcomes(db, org: Organization, rng: random.Random) -> int:
    """Recent synthetic awards become 'tracked' past lines with the broker's actual bid."""
    today = today_et()
    rows = db.execute(
        select(SolicitationLine, AwardHistory)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .join(AwardHistory, (AwardHistory.solicitation_number == Solicitation.solicitation_number) & (AwardHistory.nsn == SolicitationLine.nsn))
        .where(AwardHistory.source == "synthetic", AwardHistory.award_date >= today - timedelta(days=60))
        .limit(16)
    ).all()
    n = 0
    for ln, award in rows:
        landed = award.unit_price * rng.uniform(0.6, 0.85)
        rec_bid = round(award.unit_price * rng.uniform(0.92, 1.08), 2)
        actual = round(rec_bid * rng.uniform(0.98, 1.02), 2)
        won = actual < award.unit_price and rng.random() < 0.8
        if won:
            award.awardee_cage = org.cage_code
            award.unit_price = actual
        db.add(Recommendation(
            organization_id=org.id, solicitation_line_id=ln.id, landed_cost_per_unit=round(landed, 4), provisional=False,
            recommended_bid=rec_bid, p_win_at_recommended=round(rng.uniform(0.35, 0.65), 3),
            expected_profit=round((rec_bid - landed) * ln.quantity * 0.5, 2), no_bid=False,
            explanation={"note": "historical demo line"}, actual_bid=actual,
            actual_bid_at=datetime.now(UTC) - timedelta(days=rng.randint(20, 70)),
        ))
        n += 1
    db.flush()
    return n


def _write_credentials() -> None:
    lines = [
        "# Demo credentials",
        "",
        "Created by `make seed` (or `python dev.py seed`). Demo data only. Never reuse these anywhere.",
        "",
        "| Role | Organization | Email | Password |",
        "|---|---|---|---|",
    ]
    for o in DEMO_ORGS:
        lines.append(f"| Owner | {o['name']} | `{o['email']}` | `{o['password']}` |")
    lines.append(f"| Superadmin (/admin) | Platform Admin | `{SUPERADMIN['email']}` | `{SUPERADMIN['password']}` |")
    lines += ["", "Open http://localhost:3000 and log in. Demo orgs use the fictional CAGE codes 9ZDM1 and 9ZDM2."]
    (REPO_ROOT / "DEMO_CREDENTIALS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- main
def run_seed(force_synthetic: bool = False, skip_train: bool = False) -> None:
    from app.cli import migrate

    migrate()
    rng = random.Random(7)
    random.seed(7)
    today = today_et()
    with session_scope() as db:
        _reset_demo(db)
        real = db.execute(
            select(func.count()).select_from(AwardHistory).where(AwardHistory.source != "synthetic")
        ).scalar_one()
        use_real = real >= REAL_HISTORY_THRESHOLD and not force_synthetic
        data = generate(end=today - timedelta(days=1), n_open_lines=40)
        if use_real:
            _say(f"Found {real:,} real award rows: using real history (synthetic history skipped).")
            # Still need today's demo lines: keep only the generator's NSN metadata for those.
            data.awards = data.awards.iloc[0:0]
            _insert_synthetic(db, data)
        else:
            _say(f"Generating synthetic history: {len(data.awards):,} awards for {len(data.nsns)} NSNs ...")
            _insert_synthetic(db, data)
        open_lines = _insert_open_lines(db, data)
        orgs = _create_orgs(db)
        db.commit()
        _say(f"Inserted {len(open_lines)} open RFQ lines due {today + timedelta(days=1)} .. {today + timedelta(days=15)}.")

    if not skip_train:
        from app.services.training import train_and_maybe_promote

        with session_scope() as db:
            has_active = db.execute(select(ModelRun.id).where(ModelRun.is_active.is_(True))).first()
            res = train_and_maybe_promote(db, as_of=today, force_promote=not has_active)
            _say(f"Trained model run #{res['model_run_id']}: pinball {res['pinball_mean']:.4f}, "
                 f"80% coverage {res['coverage_80']:.0%}, promoted={res['promoted']} ({res['reason']}).")

    from app.services.recommendations import refresh_org_recommendations

    with session_scope() as db:
        orgs = [db.execute(select(Organization).where(Organization.name == o["name"])).scalar_one() for o in DEMO_ORGS]
        lines = list(db.execute(
            select(SolicitationLine).join(Solicitation).where(
                Solicitation.solicitation_number.like(f"{SYNTHETIC_PREFIX}%"), Solicitation.return_by_date >= today)
        ).scalars())
        for org in orgs:
            _create_contacts(db, org, lines, rng)
            n = refresh_org_recommendations(db, org.id, create_quote_drafts=True)
            db.flush()
            watched = [ln for ln in lines if ln.fsc in (db.get(OrgSettings, org.id).fsc_watchlist or [])]
            q = _simulate_quotes(db, org, watched, rng, share=0.6 if org is orgs[0] else 0.4)
            p = _past_outcomes(db, org, rng) if org is orgs[0] else 0
            _say(f"{org.name}: {n} recommendations, {q} supplier replies parsed, {p} past outcomes.")
        sent = db.execute(select(func.count()).select_from(QuoteRequest).where(QuoteRequest.status != QuoteRequestStatus.draft)).scalar_one()
        _say(f"Quote requests sent/answered: {sent}.")
    _write_credentials()
    _say("\nDemo ready. Credentials (also in DEMO_CREDENTIALS.md):")
    for o in DEMO_ORGS:
        _say(f"  {o['email']}  /  {o['password']}   ({o['name']})")
    _say(f"  {SUPERADMIN['email']}  /  {SUPERADMIN['password']}   (superadmin, /admin)")


__all__ = ["run_seed"]
