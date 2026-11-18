"""Synthetic DIBBS-like award history for demos and tests.

Clearly synthetic (fictional company names, generated NSNs/CAGEs), but with price dynamics that mimic
real procurement history:
  * a per-NSN base price (lognormal within an FSC-typical range) and per-NSN noise level,
  * inflation drift from the real PPI series in ml/data/ppi.csv,
  * quantity discounts (price ~ (q / typical_q) ** -elasticity),
  * an incumbent awardee who keeps winning, with occasional incumbent switches (and price resets),
  * premiums for manual (Q) solicitations, first article, approved-source-only items, set-asides.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dibbs_ml.inflation import PPI, default_ppi

# Five weapon-system supply classes DLA buys spare parts for on DIBBS (real FSCs; volumes in docs/data_sources.md):
# whole weapons and live munitions are not bought this way, their parts are.
FSC_SPECS: dict[str, dict] = {
    "2530": {  # Vehicular brake, steering, axle, wheel and track components: tank and combat-vehicle running gear
        "names": ["SHOE,TRACK,VEHICULAR", "WHEEL,ROAD,TRACKED VEHICLE", "SPROCKET,TRACK DRIVE", "IDLER WHEEL ASSEMBLY",
                  "TRACK ASSEMBLY,VEHICULAR", "SHOCK ABSORBER,TRACKED VEHICLE", "PARTS KIT,SHOE BRAKE"],
        "price": (150.0, 12000.0), "uoi": ["EA", "EA", "EA", "KT"], "pn": ["12{a:02d}{b:04d}", "19207-12{a:02d}{b:03d}",
                                                                         "MS{a:02d}{b:03d}-1"],
    },
    "1005": {  # Guns, through 30 mm: rifles and machine guns
        "names": ["CARRIER ASSEMBLY,BOLT", "BARREL ASSEMBLY,MACHINE GUN", "RECEIVER,LOWER,RIFLE", "TRIGGER ASSEMBLY",
                  "BUFFER ASSEMBLY,RIFLE", "FEEDER,AUTOMATIC GUN", "EXTRACTOR,CARTRIDGE", "BOLT ASSEMBLY,RIFLE"],
        "price": (8.0, 3500.0), "uoi": ["EA"], "pn": ["129{a:02d}{b:03d}", "8448{a:02d}{b:02d}", "5504{a:02d}{b:02d}"],
    },
    "1010": {  # Guns, over 30 mm up to 75 mm: 40 mm grenade launchers
        "names": ["FEED TRAY ASSEMBLY,40MM", "BARREL,GRENADE LAUNCHER", "SIGHT,GRENADE LAUNCHER", "CHUTE ASSEMBLY,40MM",
                  "BOLT ASSEMBLY,GRENADE LAUNCHER", "MOUNT,PINTLE", "RECOIL ADAPTER,GRENADE LAUNCHER"],
        "price": (25.0, 6000.0), "uoi": ["EA"], "pn": ["8448{a:02d}{b:02d}", "1257{a:02d}{b:02d}", "D{a:02d}{b:04d}"],
    },
    "1055": {  # Launchers, rocket and pyrotechnic
        "names": ["LAUNCHER,ROCKET", "TUBE ASSEMBLY,LAUNCHER", "FIRING MECHANISM,LAUNCHER", "DISCHARGER,SMOKE GRENADE",
                  "DETENT,ROCKET LAUNCHER", "HARNESS,WIRING,LAUNCHER"],
        "price": (40.0, 15000.0), "uoi": ["EA", "EA", "SE"], "pn": ["117{a:02d}{b:03d}", "9{a:02d}{b:04d}",
                                                                 "1302{a:02d}{b:02d}"],
    },
    "5855": {  # Night vision equipment, emitted and reflected radiation
        "names": ["IMAGE INTENSIFIER TUBE", "NIGHT VISION MONOCULAR", "THERMAL WEAPON SIGHT", "EYEPIECE ASSEMBLY",
                  "OBJECTIVE LENS ASSEMBLY", "MOUNT,NIGHT VISION DEVICE", "RECEIVER,INFRARED"],
        "price": (35.0, 14000.0), "uoi": ["EA"], "pn": ["A3{a:02d}{b:03d}", "MX-{a:02d}{b:03d}/UV",
                                                       "1349{a:02d}{b:02d}"],
    },
}
# Relative volume of each class in the generated history (rifle and vehicle parts are the most common buys).
FSC_WEIGHTS = {"2530": 1.3, "1005": 1.3, "1010": 0.8, "1055": 0.6, "5855": 0.9}

ADJ = ["Keystone", "Patriot", "Summit", "Liberty", "Ironwood", "Bluewater", "Granite", "Redstone", "Northfield",
       "Cardinal", "Harbor", "Frontier", "Allied", "Pioneer", "Sentinel", "Eagle Rock", "Tri-State", "Precision",
       "Coastal", "Heartland", "Pinnacle", "Atlas", "Meridian", "Capitol", "Lakeshore", "Silverline"]
NOUN = ["Armament", "Aerospace", "Industrial", "Defense", "Ordnance", "Tactical", "Optics", "Vehicle Systems",
        "Components", "Supply", "Parts", "Arms", "Precision", "Track & Drivetrain"]
SUFFIX = ["Supply Co.", "Inc.", "LLC", "Corp.", "Distributors", "Group", "Manufacturing", "Industries"]
LOCATIONS = [("Columbus", "OH"), ("Dayton", "OH"), ("Richmond", "VA"), ("Norfolk", "VA"), ("Philadelphia", "PA"),
             ("Wichita", "KS"), ("Tulsa", "OK"), ("Phoenix", "AZ"), ("San Diego", "CA"), ("Los Angeles", "CA"),
             ("Seattle", "WA"), ("Denver", "CO"), ("Dallas", "TX"), ("Houston", "TX"), ("Orlando", "FL"),
             ("Atlanta", "GA"), ("Chicago", "IL"), ("Detroit", "MI"), ("Hartford", "CT"), ("Long Island", "NY"),
             ("Salt Lake City", "UT"), ("Huntsville", "AL"), ("Charleston", "SC"), ("Minneapolis", "MN")]
# Fictional issuing offices ("ZZ"), so synthetic solicitation numbers never collide with real DIBBS ones.
ACTIVITIES = ["SPEZZ1", "SPEZZ2", "SPEZZ3", "SPEZZ4", "SPEZZ5", "SPEZZ6", "SPEZZ7"]
SYNTHETIC_PREFIX = "SPEZZ"
# Mix observed in real DIBBS index files (docs/data_sources.md): ~82% unrestricted, ~14% small business.
SET_ASIDES = ["NONE"] * 82 + ["SB"] * 14 + ["WOSB", "WOSB", "SDVOSB", "HUBZONE"]


@dataclass
class SyntheticData:
    nsns: pd.DataFrame
    awards: pd.DataFrame
    approved_sources: pd.DataFrame
    companies: pd.DataFrame
    open_lines: pd.DataFrame


def _cage(rng: np.random.Generator, used: set[str]) -> str:
    alnum = string.digits + "ABCDEFGHJKLMNPQRSTUVWXYZ"
    while True:
        c = str(rng.integers(1, 10)) + "".join(rng.choice(list(alnum), 4))
        if c not in used:
            used.add(c)
            return c


def _company_name(rng, used: set[str]) -> str:
    while True:
        n = f"{rng.choice(ADJ)} {rng.choice(NOUN)} {rng.choice(SUFFIX)}".replace("Supply Supply", "Supply")
        if n not in used:
            used.add(n)
            return n


def _part_number(rng, fsc: str) -> str:
    pat = str(rng.choice(FSC_SPECS[fsc]["pn"]))
    return pat.format(a=int(rng.integers(2, 20)), b=int(rng.integers(1, 30)))


_USED_SOLS: set[str] = set()


def _sol_number(rng, activity: str, d: date, type_code: str) -> str:
    while True:
        sn = f"{activity}{d.year % 100:02d}{type_code}{int(rng.integers(0, 10000)):04d}"
        if sn not in _USED_SOLS:
            _USED_SOLS.add(sn)
            return sn


def generate(
    n_nsns: int = 300,
    start: date = date(2023, 1, 1),
    end: date | None = None,
    n_open_lines: int = 40,
    seed: int = 20240601,
    ppi: PPI | None = None,
    fscs: list[str] | None = None,
) -> SyntheticData:
    rng = np.random.default_rng(seed)
    _USED_SOLS.clear()
    ppi = ppi or default_ppi()
    end = end or date.today() - timedelta(days=1)
    fscs = fscs or list(FSC_SPECS)
    span_years = (end - start).days / 365.25

    used_cages: set[str] = set()
    used_names: set[str] = set()
    companies = []
    for _ in range(140):
        city, state = LOCATIONS[int(rng.integers(len(LOCATIONS)))]
        companies.append({"cage_code": _cage(rng, used_cages), "name": _company_name(rng, used_names),
                          "city": city, "state": state, "country": "USA", "is_manufacturer_guess": False})
    manufacturers = companies[:45]
    distributors = companies[45:]
    for c in manufacturers:
        c["is_manufacturer_guess"] = True

    nsns, awards, sources = [], [], []
    used_nsn: set[str] = set()
    fsc_weights = np.array([FSC_WEIGHTS.get(f, 1.0) for f in fscs])
    fsc_choice = rng.choice(fscs, size=n_nsns, p=fsc_weights / fsc_weights.sum())
    for fsc in fsc_choice:
        spec = FSC_SPECS[fsc]
        while True:
            niin = f"01{int(rng.integers(0, 10_000_000)):07d}"
            nsn = f"{fsc}{niin}"
            if nsn not in used_nsn:
                used_nsn.add(nsn)
                break
        lo, hi = spec["price"]
        base = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
        uoi = str(rng.choice(spec["uoi"]))
        if uoi in ("KT", "SE"):
            base *= float(rng.uniform(1.5, 4))
        aso = bool(rng.random() < 0.35)
        amsc = str(rng.choice(["C", "B", "P"])) if aso else str(rng.choice(["G", "G", "G", "T"]))
        n_src = int(rng.integers(1, 4))
        mfrs = rng.choice(len(manufacturers), size=n_src, replace=False)
        for m in mfrs:
            sources.append({"nsn": nsn, "cage_code": manufacturers[m]["cage_code"], "part_number": _part_number(rng, fsc)})
        rate = float(np.clip(rng.lognormal(np.log(4.0), 0.8), 0.4, 18))  # awards/year
        typical_q = float(np.clip(rng.lognormal(np.log(120 if base < 50 else 30 if base < 500 else 8 if base < 3000 else 3),
                                                0.9), 1, 5000))
        rec = {
            "nsn": nsn, "fsc": fsc, "niin": niin, "nomenclature": str(rng.choice(spec["names"])),
            "unit_of_issue": uoi, "amsc": amsc, "approved_source_only": aso, "n_approved_sources": n_src,
            "base_price": base, "sigma": float(rng.uniform(0.07, 0.28)), "elasticity": float(rng.uniform(0.04, 0.22)),
            "typical_qty": typical_q, "rate": rate,
            "dod_standard_price": round(base * ppi.factor(fsc, start, end) * float(rng.uniform(1.05, 1.7)), 2),
        }
        nsns.append(rec)

        # Awardee pool and incumbent dynamics.
        pool = [distributors[i]["cage_code"] for i in rng.choice(len(distributors), size=int(rng.integers(2, 7)),
                                                                  replace=False)]
        if aso and rng.random() < 0.5:
            pool.append(manufacturers[mfrs[0]]["cage_code"])
        incumbent = pool[0]
        level = 1.0  # incumbent-specific price level, resets on switches
        n_awards = int(rng.poisson(rate * span_years))
        days = np.sort(rng.integers(0, max((end - start).days, 1), size=n_awards))
        for dd in days:
            award_date = start + timedelta(days=int(dd))
            if rng.random() < 0.12:
                incumbent = str(rng.choice(pool))
                level = float(rng.uniform(0.85, 1.12))
            awardee = incumbent if rng.random() < 0.75 else str(rng.choice(pool))
            qty = float(max(1, round(typical_q * rng.lognormal(0, 0.8))))
            type_code = str(rng.choice(["T", "T", "T", "U", "Q", "Q"]))
            first_article = bool(rng.random() < 0.04)
            set_aside = str(rng.choice(SET_ASIDES))
            inspection = "ORIGIN" if rng.random() < 0.25 else "DESTINATION"
            price = (
                base
                * ppi.factor(fsc, start, award_date)
                * (qty / typical_q) ** (-rec["elasticity"])
                * level
                * (1.06 if type_code == "Q" else 1.0)
                * (1.25 if first_article else 1.0)
                * (1.08 if aso else 1.0)
                * (1.04 if set_aside != "NONE" else 1.0)
                * (1.03 if inspection == "ORIGIN" else 1.0)
                * float(rng.lognormal(0, rec["sigma"]))
            )
            price = round(max(price, 0.01), 2 if price >= 1 else 4)
            activity = str(rng.choice(ACTIVITIES))
            issue = award_date - timedelta(days=int(rng.integers(10, 40)))
            awards.append(
                {
                    "nsn": nsn, "fsc": fsc, "award_date": award_date,
                    "contract_number": f"{activity}{award_date.year % 100:02d}P{int(rng.integers(0, 100000)):05d}",
                    "awardee_cage": awardee, "quantity": qty, "unit_of_issue": uoi, "unit_price": price,
                    "total_value": round(price * qty, 2),
                    "solicitation_number": _sol_number(rng, activity, issue, type_code),
                    "issue_date": issue, "type_code": type_code, "buyer_code": f"{activity[-3:]}{rng.choice(list('ABCDEFGH'))}",
                    "set_aside": set_aside, "inspection_point": inspection,
                    "fob": "DESTINATION" if rng.random() < 0.8 else "ORIGIN", "first_article": first_article,
                    "fast_award": bool(type_code in "TU" and price * qty < 25000 and rng.random() < 0.5),
                    "aidc": bool(rng.random() < 0.3), "amsc": amsc, "approved_source_only": aso,
                    "n_approved_sources": n_src, "dod_standard_price": rec["dod_standard_price"],
                    "unit_of_issue_ctx": uoi,
                }
            )
        rec["_incumbent"] = incumbent
        rec["_level"] = level

    nsn_df = pd.DataFrame(nsns)
    award_df = pd.DataFrame(awards).drop(columns=["unit_of_issue_ctx"])
    award_df = award_df.drop_duplicates(subset=["nsn", "contract_number", "award_date", "quantity"])

    # Today's open RFQ lines: mostly NSNs with history, a few never-awarded (cold start).
    open_lines = []
    with_hist = award_df.groupby("nsn").size()
    warm = list(with_hist[with_hist >= 2].index)
    cold = [n for n in nsn_df["nsn"] if n not in set(with_hist.index)]
    # Spread the warm lines evenly over the supply classes (a demo shows every category), then top up at random.
    n_warm = min(n_open_lines - 4, len(warm))
    per_fsc = n_warm // len(fscs)
    picks: list[str] = []
    for f in fscs:
        pool = [n for n in warm if n[:4] == f]
        if per_fsc and pool:
            picks += list(rng.choice(pool, size=min(per_fsc, len(pool)), replace=False))
    rest = [n for n in warm if n not in set(picks)]
    if n_warm > len(picks) and rest:
        picks += list(rng.choice(rest, size=min(n_warm - len(picks), len(rest)), replace=False))
    picks += cold[:4] if len(cold) >= 4 else list(rng.choice(nsn_df["nsn"], size=4, replace=False))
    today = end + timedelta(days=1)
    for i, nsn in enumerate(picks):
        r = nsn_df[nsn_df["nsn"] == nsn].iloc[0]
        type_code = str(rng.choice(["T", "T", "U", "Q"]))
        activity = str(rng.choice(ACTIVITIES))
        qty = float(max(1, round(r["typical_qty"] * rng.lognormal(0, 0.7))))
        open_lines.append(
            {
                "solicitation_number": _sol_number(rng, activity, today, type_code), "nsn": nsn, "fsc": r["fsc"],
                "niin": r["niin"], "nomenclature": r["nomenclature"], "quantity": qty,
                "unit_of_issue": r["unit_of_issue"], "amsc": r["amsc"],
                "approved_source_only": bool(r["approved_source_only"]),
                "dod_standard_price": float(r["dod_standard_price"]), "type_code": type_code,
                "buyer_code": f"{activity[-3:]}{rng.choice(list('ABCDEFGH'))}", "issue_date": today - timedelta(days=int(rng.integers(0, 3))),
                "return_by_date": today + timedelta(days=int(rng.integers(1, 15))),
                "set_aside": str(rng.choice(SET_ASIDES)), "inspection_point": "ORIGIN" if rng.random() < 0.2 else "DESTINATION",
                "fob": "DESTINATION", "first_article": bool(rng.random() < 0.05),
                "fast_award": bool(type_code in "TU" and rng.random() < 0.5), "aidc": bool(rng.random() < 0.3),
                "delivery_days": int(rng.choice([30, 45, 60, 90, 120])),
                "_true_next_price": float(
                    r["base_price"] * ppi.factor(r["fsc"], start, today) * (qty / r["typical_qty"]) ** (-r["elasticity"])
                    * float(rng.lognormal(0, r["sigma"]))
                ),
                "_line_index": i,
            }
        )
    return SyntheticData(
        nsns=nsn_df,
        awards=award_df.sort_values(["award_date", "nsn"]).reset_index(drop=True),
        approved_sources=pd.DataFrame(sources).drop_duplicates(),
        companies=pd.DataFrame(companies),
        open_lines=pd.DataFrame(open_lines),
    )
