"""USAspending API: DLA award totals by PIID, as a sanity check on scraped award values.

No key needed. ~90-day publication lag (DoD FPDS delay); totals only, no line-level prices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.connectors.base import PoliteHttp

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Start Date", "Contract Award Type",
          "Description", "generated_internal_id"]


@dataclass
class SpendingAward:
    piid: str
    recipient_name: str | None
    recipient_uei: str | None
    amount: float | None
    start_date: str | None
    award_type: str | None


def parse_search(payload: dict) -> list[SpendingAward]:
    out = []
    for r in payload.get("results", []):
        out.append(
            SpendingAward(
                piid=str(r.get("Award ID") or ""),
                recipient_name=r.get("Recipient Name"),
                recipient_uei=r.get("Recipient UEI"),
                amount=float(r["Award Amount"]) if r.get("Award Amount") is not None else None,
                start_date=r.get("Start Date"),
                award_type=r.get("Contract Award Type"),
            )
        )
    return out


class USAspendingClient:
    def __init__(self, http: PoliteHttp | None = None, mode: str | None = None) -> None:
        self.mode = mode or get_settings().connector_mode
        self.http = http or PoliteHttp("usaspending")

    def awards_by_piid(self, piids: list[str]) -> list[SpendingAward]:
        if self.mode != "live":
            p = Path(get_settings().fixtures_dir) / "usaspending" / "spending_by_award_piid_keyword.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            payload = data.get("response", data)
            return [a for a in parse_search(payload) if not piids or a.piid in piids]
        out: list[SpendingAward] = []
        for i in range(0, len(piids), 25):
            body = {
                "filters": {"keywords": piids[i : i + 25], "award_type_codes": ["A", "B", "C", "D"]},
                "fields": FIELDS,
                "limit": 100,
                "page": 1,
                "subawards": False,
            }
            r = self.http.post(SEARCH_URL, json=body)
            r.raise_for_status()
            self.http.save_raw(f"usaspending_{i}.json", r.content)
            out.extend(parse_search(r.json()))
        return out
