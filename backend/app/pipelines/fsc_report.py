"""ml/reports/fsc_volume.md: rank FSCs to pick the niche.

Columns: solicitations, share automated (T/U), median award value, share of NSNs with >= 3 historical
awards, distinct historical awardees. Real (non-synthetic) data only; synthetic demo rows are excluded.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.models import AwardHistory, Solicitation, SolicitationLine

REPORT = REPO_ROOT / "ml" / "reports" / "fsc_volume.md"


def fsc_stats(db: Session) -> list[dict]:
    sols: dict[str, set[str]] = defaultdict(set)
    auto: dict[str, set[str]] = defaultdict(set)
    nsns: dict[str, set[str]] = defaultdict(set)
    for fsc, sn, tc, nsn in db.execute(
        select(SolicitationLine.fsc, Solicitation.solicitation_number, Solicitation.type_code, SolicitationLine.nsn)
        .join(Solicitation)
        .where(~Solicitation.solicitation_number.like("SPEZZ%"))
    ).all():
        sols[fsc].add(sn)
        nsns[fsc].add(nsn)
        if tc in ("T", "U"):
            auto[fsc].add(sn)
    values: dict[str, list[float]] = defaultdict(list)
    awards_per_nsn: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    awardees: dict[str, set[str]] = defaultdict(set)
    for nsn, qty, price, total, cage in db.execute(
        select(AwardHistory.nsn, AwardHistory.quantity, AwardHistory.unit_price, AwardHistory.total_value,
               AwardHistory.awardee_cage).where(AwardHistory.source != "synthetic")
    ).all():
        fsc = nsn[:4]
        values[fsc].append(total if total else qty * price)
        awards_per_nsn[fsc][nsn] += 1
        if cage:
            awardees[fsc].add(cage)
    out = []
    for fsc in sorted(set(sols) | set(values)):
        hist_nsns = set(awards_per_nsn[fsc]) | nsns[fsc]
        n3 = sum(1 for n in hist_nsns if awards_per_nsn[fsc].get(n, 0) >= 3)
        out.append(
            {
                "fsc": fsc,
                "solicitations": len(sols[fsc]),
                "share_automated": (len(auto[fsc]) / len(sols[fsc])) if sols[fsc] else None,
                "median_award_value": statistics.median(values[fsc]) if values[fsc] else None,
                "share_nsns_3plus_awards": (n3 / len(hist_nsns)) if hist_nsns else None,
                "distinct_awardees": len(awardees[fsc]),
                "nsns": len(hist_nsns),
                "award_rows": len(values[fsc]),
            }
        )
    out.sort(key=lambda r: -r["solicitations"])
    return out


def _pct(v) -> str:
    return "n/a" if v is None else f"{100 * v:.0f}%"


def write_fsc_volume_report(db: Session, path: Path | None = None) -> Path:
    path = path or REPORT
    stats = fsc_stats(db)
    watch = set(get_settings().default_fsc_watchlist)
    lines = [
        "# FSC volume report",
        "",
        f"Generated {date.today().isoformat()} from the database (real DIBBS data only; synthetic SPEZZ* demo rows excluded).",
        "",
        "Ranked by number of solicitations. `*` marks the current default watchlist.",
        "",
        "| FSC | Solicitations | Automated (T/U) | Median award value | NSNs with >= 3 awards | Distinct awardees | NSNs | Award rows |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in stats:
        mv = "n/a" if r["median_award_value"] is None else f"${r['median_award_value']:,.0f}"
        lines.append(
            f"| {r['fsc']}{' *' if r['fsc'] in watch else ''} | {r['solicitations']:,} | {_pct(r['share_automated'])} | {mv} | "
            f"{_pct(r['share_nsns_3plus_awards'])} | {r['distinct_awardees']:,} | {r['nsns']:,} | {r['award_rows']:,} |"
        )
    if not stats:
        lines.append("| (no real data yet: run `make ingest` / `make backfill`) | | | | | | | |")
    idx = Path(get_settings().fixtures_dir) / "dibbs" / "rfq_index" / "index_stats.json"
    if idx.exists():
        lines += [
            "",
            "## Observed in the captured index files",
            "",
            "From the five daily RFQ index files captured during data discovery (see docs/data_sources.md):",
            "",
            "```json",
            json.dumps(json.loads(idx.read_text(encoding="utf-8")).get("target_fscs", {}), indent=2)[:4000],
            "```",
        ]
    lines += [
        "",
        "## How to read this",
        "",
        "- **Automated share** high = more fast/auto awards decided on price: the pricing model matters most there.",
        "- **NSNs with >= 3 awards** = how often the model has real NSN history (vs. cold start).",
        "- **Distinct awardees** = competition; very few awardees with high incumbent share are hard to break into.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
