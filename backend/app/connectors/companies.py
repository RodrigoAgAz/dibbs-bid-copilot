"""CAGE -> company resolution.

1. DIBBS public CAGE pop-up (/Refs/cage.aspx, behind the DIBBS banner only) - verified.
2. SAM.gov Entity Management API (needs SAM_API_KEY) - request/response shape from public docs, UNVERIFIED.
cage.dla.mil is NOT used: every page requires accepting a usage agreement (docs/data_sources.md section 9).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import PoliteHttp
from app.connectors.dibbs import DibbsSource
from app.connectors.parsers.dibbs_html import parse_dibbs_cage
from app.models import Company

log = logging.getLogger(__name__)
SAM_URL = "https://api.sam.gov/entity-information/v3/entities"


def parse_sam_entity(payload: dict) -> dict | None:
    data = (payload or {}).get("entityData") or []
    if not data:
        return None
    e = data[0]
    reg = e.get("entityRegistration") or {}
    core = e.get("coreData") or {}
    addr = core.get("physicalAddress") or {}
    return {
        "name": reg.get("legalBusinessName"),
        "sam_uei": reg.get("ueiSAM"),
        "address": " ".join(x for x in (addr.get("addressLine1"), addr.get("addressLine2")) if x) or None,
        "city": addr.get("city"),
        "state": addr.get("stateOrProvinceCode"),
        "country": addr.get("countryCode"),
    }


class SamClient:
    def __init__(self, api_key: str, http: PoliteHttp | None = None) -> None:
        self.key = api_key
        self.http = http or PoliteHttp("sam")

    def entity_by_cage(self, cage: str) -> dict | None:
        r = self.http.get(SAM_URL, params={"api_key": self.key, "cageCode": cage, "includeSections": "entityRegistration,coreData"})
        if r.status_code != 200:
            log.warning("SAM lookup for %s failed: %s", cage, r.status_code)
            return None
        self.http.save_raw(f"sam_{cage}.json", r.content)
        return parse_sam_entity(r.json())


def resolve_company(db: Session, source: DibbsSource, cage: str, refresh: bool = False) -> Company | None:
    comp = db.get(Company, cage)
    if comp is not None and comp.name and not refresh:
        return comp
    info = None
    try:
        page = source.cage_page(cage)
        info = parse_dibbs_cage(page) if page else None
    except Exception as exc:
        log.warning("DIBBS CAGE lookup failed for %s: %s", cage, exc)
    if comp is None:
        comp = Company(cage_code=cage)
        db.add(comp)
    if info:
        comp.name, comp.address, comp.city, comp.state, comp.country = info.name, info.address, info.city, info.state, info.country
        comp.source = "dibbs_cage"
    key = get_settings().sam_api_key
    if key:
        try:
            sam = SamClient(key).entity_by_cage(cage)
            if sam:
                comp.sam_uei = sam["sam_uei"]
                comp.name = comp.name or sam["name"]
                comp.city, comp.state = comp.city or sam["city"], comp.state or sam["state"]
                comp.source = (comp.source or "") + "+sam"
        except Exception as exc:
            log.warning("SAM lookup failed for %s: %s", cage, exc)
    comp.resolved_at = datetime.now(UTC)
    return comp
