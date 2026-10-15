"""Contestability: can a broker win this line at all, whatever the price model says?

Version 2: the share of the part's awards won by resellers (brokers and distributors, as opposed to approved-source
manufacturers), shrunk toward the supply class's reseller share when the part has few awards
(dibbs_ml.competitors.FscCompetitors.for_line). The broker's own past wins count as reseller wins. The full
competitor picture (incumbents, supply-class players, how far below the last price each wins, and the competitor
view of P(win)) travels with it so the RFQ page can show who the broker is up against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Organization, SolicitationLine
from app.services.competitors import fsc_competitors
from dibbs_ml.competitors import LineCompetitors


@dataclass
class Contestability:
    p_winnable: float
    reason: str
    incumbent_cage: str | None = None
    incumbent_share: float | None = None
    n_awards: int = 0
    incumbent_is_manufacturer: bool = False
    reseller_share_nsn: float | None = None
    reseller_share_fsc: float | None = None
    competitors: LineCompetitors | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "competitors"}
        d["p_winnable"] = round(self.p_winnable, 3)
        for k in ("incumbent_share", "reseller_share_nsn", "reseller_share_fsc"):
            if d[k] is not None:
                d[k] = round(d[k], 3)
        return d


def contestability(db: Session, line: SolicitationLine, org_id: int | None = None) -> Contestability:
    org_cage = None
    if org_id is not None:
        org = db.get(Organization, org_id)
        org_cage = org.cage_code if org else None
    lc = fsc_competitors(db, line.fsc).for_line(line.nsn, org_cage=org_cage)
    top = lc.incumbents[0] if lc.incumbents else None
    return Contestability(
        p_winnable=lc.p_winnable,
        reason=lc.reason,
        incumbent_cage=top.cage if top else None,
        incumbent_share=top.share if top else None,
        n_awards=lc.n_awards,
        incumbent_is_manufacturer=bool(top and top.is_manufacturer),
        reseller_share_nsn=lc.reseller_share_nsn,
        reseller_share_fsc=lc.reseller_share_fsc,
        competitors=lc,
    )
