"""Enrichment imports: FLIS / PUB LOG item characteristics (public) and a distributor's catalog prices (per tenant).

Both read delimited text files (CSV or TSV) with a header row and match columns by name, case-insensitively, so an
export from PUB LOG, a spreadsheet, or a distributor's price list loads without reformatting:

* characteristics: NSN or NIIN (13 or 9 digits, hyphens allowed), MRC (or "code"), value (or "reply", "clear text"),
  optional name (or "requirement"). The live PUB LOG layout is UNVERIFIED (dla.mil refuses non-browser downloads);
  see docs/data_sources.md for how to obtain the data and what to check.
* catalog: part number (or "mpn", "manufacturer part number"), price (or "unit price", "list price"), optional
  manufacturer CAGE ("cage", "mfr cage"), unit ("uom", "price unit"), basis quantity ("per", "price per"),
  minimum order ("moq"), lead time days, in stock (yes/no).

Idempotent: rows are upserted on (nsn, mrc) and (org, supplier, manufacturer, normalized part number).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogPrice, ItemCharacteristic

CHAR_COLUMNS = {
    "nsn": ("nsn", "niin", "national stock number", "stock number"),
    "mrc": ("mrc", "code", "master requirement code", "requirement code"),
    "name": ("name", "requirement", "requirement name", "characteristic", "mrc name"),
    "value": ("value", "reply", "clear text reply", "clear text", "decoded reply", "characteristic value"),
}
CATALOG_COLUMNS = {
    "part_number": ("part number", "part_number", "mpn", "manufacturer part number", "mfr part number", "pn", "p/n", "part no", "part #"),
    "price": ("price", "unit price", "unit_price", "list price", "your price", "net price", "cost"),
    "manufacturer_cage": ("manufacturer cage", "mfr cage", "cage", "cage code", "manufacturer_cage", "mfr_cage"),
    "price_unit": ("uom", "unit", "price unit", "price_unit", "unit of measure"),
    "price_basis_qty": ("per", "price per", "price_basis_qty", "basis", "pack qty", "pack"),
    "min_order_qty": ("moq", "min order", "minimum order", "min_order_qty", "minimum order quantity"),
    "lead_time_days": ("lead time", "lead time days", "lead_time_days", "lead days", "aro"),
    "in_stock": ("in stock", "stock", "in_stock", "available", "availability"),
}


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (h or "").strip().lower())


def _pick(headers: list[str], names: tuple[str, ...]) -> int | None:
    norm = [_norm_header(h) for h in headers]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


def _rows(text: str) -> tuple[list[str], list[list[str]]]:
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ("|" if sample.count("|") > sample.count(",") else ",")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _nsn(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 13:
        return digits
    return None  # a bare NIIN (9 digits) has no FSC: the caller supplies one via the fsc column, or the row is skipped


def _num(v: str) -> float | None:
    s = re.sub(r"[^0-9.\-]", "", v or "")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def norm_pn(pn: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (pn or "").upper())


def import_characteristics(db: Session, text: str, source: str = "pub_log", fsc_column: str | None = None) -> dict:
    """Load item characteristics from delimited text. Returns counts."""
    headers, rows = _rows(text)
    if not headers:
        return {"rows": 0, "loaded": 0, "skipped": 0, "error": "empty file"}
    i_nsn, i_mrc = _pick(headers, CHAR_COLUMNS["nsn"]), _pick(headers, CHAR_COLUMNS["mrc"])
    i_name, i_val = _pick(headers, CHAR_COLUMNS["name"]), _pick(headers, CHAR_COLUMNS["value"])
    i_fsc = _pick(headers, (fsc_column,)) if fsc_column else _pick(headers, ("fsc", "federal supply class"))
    if i_nsn is None or i_mrc is None or i_val is None:
        return {"rows": len(rows), "loaded": 0, "skipped": len(rows),
                "error": f"need NSN/NIIN, MRC and value columns; got {headers}"}
    now = datetime.now(UTC)
    existing = {(c.nsn, c.mrc): c for c in db.execute(select(ItemCharacteristic)).scalars()}
    loaded = skipped = 0
    for r in rows:
        raw = r[i_nsn] if i_nsn < len(r) else ""
        nsn = _nsn(raw)
        if nsn is None:
            digits = re.sub(r"\D", "", raw)
            fsc = re.sub(r"\D", "", r[i_fsc]) if (i_fsc is not None and i_fsc < len(r)) else ""
            nsn = (fsc + digits) if len(digits) == 9 and len(fsc) == 4 else None
        mrc = (r[i_mrc] if i_mrc < len(r) else "").strip().upper()[:8]
        value = (r[i_val] if i_val < len(r) else "").strip()[:300]
        if not nsn or not mrc or not value:
            skipped += 1
            continue
        name = (r[i_name].strip()[:120] if (i_name is not None and i_name < len(r) and r[i_name].strip()) else None)
        row = existing.get((nsn, mrc))
        if row is None:
            row = ItemCharacteristic(nsn=nsn, mrc=mrc)
            db.add(row)
            existing[(nsn, mrc)] = row
        row.name, row.value, row.source, row.loaded_at = name, value, source, now
        loaded += 1
    db.flush()
    return {"rows": len(rows), "loaded": loaded, "skipped": skipped}


def import_catalog(db: Session, org_id: int, supplier_cage: str, text: str, source_file: str | None = None,
                   default_manufacturer_cage: str | None = None) -> dict:
    """Load a distributor's price list for one organization. Returns counts."""
    headers, rows = _rows(text)
    if not headers:
        return {"rows": 0, "loaded": 0, "skipped": 0, "error": "empty file"}
    idx = {k: _pick(headers, names) for k, names in CATALOG_COLUMNS.items()}
    if idx["part_number"] is None or idx["price"] is None:
        return {"rows": len(rows), "loaded": 0, "skipped": len(rows), "error": f"need part number and price columns; got {headers}"}

    def col(r: list[str], key: str) -> str:
        i = idx[key]
        return r[i].strip() if (i is not None and i < len(r)) else ""

    supplier = supplier_cage.strip().upper()
    now = datetime.now(UTC)
    existing = {
        (c.manufacturer_cage, c.part_number_norm): c
        for c in db.execute(select(CatalogPrice).where(CatalogPrice.organization_id == org_id,
                                                        CatalogPrice.supplier_cage == supplier)).scalars()
    }
    loaded = skipped = 0
    for r in rows:
        pn = col(r, "part_number")
        price = _num(col(r, "price"))
        if not pn or price is None or price <= 0:
            skipped += 1
            continue
        mfr = (col(r, "manufacturer_cage") or default_manufacturer_cage or "").upper()[:5] or None
        key = (mfr, norm_pn(pn))
        row = existing.get(key)
        if row is None:
            row = CatalogPrice(organization_id=org_id, supplier_cage=supplier, manufacturer_cage=mfr, part_number=pn[:100],
                               part_number_norm=norm_pn(pn)[:100], unit_price=price)
            db.add(row)
            existing[key] = row
        row.part_number, row.unit_price = pn[:100], price
        row.price_unit = (col(r, "price_unit").upper()[:8] or None)
        row.price_basis_qty = _num(col(r, "price_basis_qty"))
        row.min_order_qty = _num(col(r, "min_order_qty"))
        lt = _num(col(r, "lead_time_days"))
        row.lead_time_days = int(lt) if lt is not None else None
        stock = col(r, "in_stock").lower()
        row.in_stock = None if not stock else stock in ("y", "yes", "true", "1", "in stock", "available") or (_num(stock) or 0) > 0
        row.source_file, row.loaded_at = (source_file or None), now
        loaded += 1
    db.flush()
    return {"rows": len(rows), "loaded": loaded, "skipped": skipped, "supplier_cage": supplier}


def read_text(path: str | Path) -> str:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")
