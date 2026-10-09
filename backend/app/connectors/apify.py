"""Optional fallback: daily DIBBS RFQs from a public Apify actor (needs APIFY_TOKEN + APIFY_DIBBS_ACTOR).

UNVERIFIED: no token was available during the build, so the actor's output field names are mapped
defensively (several common spellings per field). Only the RFQ index is sourced from Apify; record
pages, PDFs and awards still come from DIBBS directly (or fixtures).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from app.config import get_settings
from app.connectors.base import PoliteHttp
from app.connectors.dibbs import FixtureDibbsSource, LiveDibbsSource
from app.connectors.parsers.dibbs_files import IndexLine

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def _pick(item: dict, *keys: str, default=""):
    for k in keys:
        for cand in (k, k.lower(), k.replace("_", "")):
            if cand in item and item[cand] not in (None, ""):
                return item[cand]
    return default


def _date(v) -> date | None:
    if not v:
        return None
    s = str(v)[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_item(item: dict) -> IndexLine | None:
    sol = re.sub(r"[^0-9A-Z]", "", str(_pick(item, "solicitationNumber", "solicitation_number", "solicitation")).upper())
    nsn = re.sub(r"[^0-9A-Z]", "", str(_pick(item, "nsn", "NSN", "nsnPartNumber", "partNumber")).upper())
    if not sol or not nsn:
        return None
    qty = _pick(item, "quantity", "qty", default=0)
    sa = str(_pick(item, "setAside", "set_aside", default="N"))[:1].upper() or "N"
    return IndexLine(
        solicitation_number=sol,
        item=nsn,
        purchase_request=str(_pick(item, "purchaseRequest", "pr", "prNumber")),
        return_by_date=_date(_pick(item, "returnByDate", "return_by_date", "returnBy", "closeDate")),
        file_name=f"{sol}.pdf",
        quantity=int(float(qty or 0)),
        unit_of_issue=str(_pick(item, "unitOfIssue", "ui", "uoi")),
        nomenclature=str(_pick(item, "nomenclature", "description")),
        buyer_code=str(_pick(item, "buyerCode", "buyer")),
        amsc=str(_pick(item, "amsc", "AMSC")),
        item_type="1" if nsn.isdigit() and len(nsn) == 13 else "2",
        set_aside_code=sa if sa in "NYHRLAE" else "N",
        set_aside_pct=100 if sa not in ("N", "") else 0,
    )


class ApifyDibbsSource(LiveDibbsSource):
    mode = "apify"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.apify_token and s.apify_dibbs_actor):
            log.warning("APIFY_TOKEN / APIFY_DIBBS_ACTOR not set; Apify source falls back to fixtures for the index")
        super().__init__()
        self.api_http = PoliteHttp("apify")
        self._fixture = FixtureDibbsSource()

    def index_lines(self, d: date) -> list[IndexLine]:
        s = get_settings()
        if not (s.apify_token and s.apify_dibbs_actor):
            return self._fixture.index_lines(d)
        resp = self.api_http.post(
            API.format(actor=s.apify_dibbs_actor.replace("/", "~")),
            params={"token": s.apify_token},
            json={"postDate": d.isoformat(), "date": d.strftime("%m-%d-%Y")},
            timeout=300,
        )
        resp.raise_for_status()
        self.api_http.save_raw(f"apify_{d:%y%m%d}.json", resp.content)
        return [x for x in (normalize_item(i) for i in resp.json()) if x is not None]

    def rfq_index_dates(self) -> list[date]:
        s = get_settings()
        if not (s.apify_token and s.apify_dibbs_actor):
            return self._fixture.rfq_index_dates()
        return super().rfq_index_dates()
