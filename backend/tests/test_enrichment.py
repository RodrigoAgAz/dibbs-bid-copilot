"""Enrichment imports (FLIS characteristics, distributor catalogs), their use in the item index and the cost model."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.models import ApprovedSource, CatalogPrice, ItemCharacteristic, Recommendation, SolicitationLine
from app.pipelines.enrichment import import_catalog, import_characteristics, read_text
from app.services.cost_model import catalog_prices_for, tenant_catalog
from app.services.item_index import build_item_index, clear_item_index
from app.services.recommendations import refresh_line_for_org
from dibbs_ml.items import ItemIndex
from tests.conftest import signup
from tests.helpers import build_world

FIX = Path(__file__).parent / "fixtures" / "enrichment"


def test_import_characteristics_matches_columns_by_name_and_upserts(db):
    out = import_characteristics(db, read_text(FIX / "characteristics_sample.csv"))
    db.commit()
    assert out == {"rows": 9, "loaded": 8, "skipped": 1}
    rows = db.execute(select(ItemCharacteristic).where(ItemCharacteristic.nsn == "5305011209897")).scalars().all()
    assert {r.mrc: r.value for r in rows} == {"MATT": "STEEL COMP 4140", "AAGR": "0.250 INCHES", "ABHP": "20"}
    assert rows[0].name in ("MATERIAL", "THREAD SIZE", "THREAD QUANTITY PER INCH")
    # Re-import is idempotent; a NIIN + FSC layout loads too; unknown layouts are refused with the headers.
    assert import_characteristics(db, read_text(FIX / "characteristics_sample.csv"))["loaded"] == 8
    assert db.query(ItemCharacteristic).count() == 8
    out = import_characteristics(db, "FSC\tNIIN\tMRC\tREPLY\n5310\t015432453\tMATT\tBRASS\n")
    assert out["loaded"] == 1 and db.get(ItemCharacteristic, db.execute(
        select(ItemCharacteristic.id).where(ItemCharacteristic.nsn == "5310015432453")).scalar_one()).value == "BRASS"
    assert "need NSN" in import_characteristics(db, "a,b\n1,2\n")["error"]


def test_characteristics_make_same_name_parts_neighbors():
    lines = pd.DataFrame([
        ("5305011209897", "SCREW,MACHINE", "EA"), ("5305010560169", "SCREW,MACHINE", "EA"),
        ("5305011635794", "SCREW,MACHINE", "EA"), ("5305019999999", "BOLT,MACHINE", "EA"),
    ], columns=["nsn", "nomenclature", "unit_of_issue"])
    chars = pd.DataFrame([
        ("5305011209897", "MATT", "STEEL COMP 4140"), ("5305011209897", "AAGR", "0.250 INCHES"), ("5305011209897", "ABHP", "20"),
        ("5305010560169", "MATT", "STEEL COMP 4140"), ("5305010560169", "AAGR", "0.250 INCHES"), ("5305010560169", "ABHP", "20"),
        ("5305011635794", "MATT", "STAINLESS STEEL"), ("5305011635794", "AAGR", "0.250 INCHES"),
        ("5305019999999", "MATT", "STEEL COMP 4140"), ("5305019999999", "AAGR", "0.250 INCHES"), ("5305019999999", "ABHP", "20"),
    ], columns=["nsn", "mrc", "value"])
    idx = ItemIndex.from_frames(lines, None, characteristics=chars)
    nbrs = idx.neighbors("5305011209897")
    assert [n for n, _, _ in nbrs] == ["5305010560169"]  # three matching characteristics, same name
    assert nbrs[0][2] == "matching characteristics" and nbrs[0][1] >= 0.6
    # One shared characteristic (thread size) is not enough; a different name (BOLT) never is.
    assert "5305011635794" not in {n for n, _, _ in nbrs} and "5305019999999" not in {n for n, _, _ in nbrs}
    # Without characteristics the index behaves as before (no family: no neighbors).
    assert ItemIndex.from_frames(lines, None).neighbors("5305011209897") == []


def test_import_catalog_and_the_cost_model_uses_it(client, db):
    build_world(db, n_open=6)
    me = signup(client, "cat@broker.example.com", org="Catalog Org")
    org_id = me["organization"]["id"]
    out = import_catalog(db, org_id, "1dist", read_text(FIX / "catalog_sample.csv"), source_file="catalog_sample.csv")
    db.commit()
    assert out["loaded"] == 3 and out["skipped"] == 1 and out["supplier_cage"] == "1DIST"
    rows = {r.part_number_norm: r for r in db.execute(select(CatalogPrice).where(CatalogPrice.organization_id == org_id)).scalars()}
    assert rows["MS24693C274"].manufacturer_cage == "96906" and rows["MS24693C274"].price_basis_qty == 100
    assert rows["AN960416"].in_stock is False and rows["NAS135248"].in_stock is True and rows["NAS135248"].lead_time_days == 10
    cat = tenant_catalog(db, org_id)
    assert cat[("96906", "MS24693C274")] == [pytest.approx(0.125)]  # per 100 -> per each
    assert catalog_prices_for(cat, [("96906", "MS24693-C274")]) == [pytest.approx(0.125)]
    assert catalog_prices_for(cat, [("ZZZZZ", "MS24693-C274")]) == [pytest.approx(0.125)]  # part-number-only fallback
    assert catalog_prices_for(cat, [("96906", "OTHER-1")]) == []
    # Re-import updates in place.
    assert import_catalog(db, org_id, "1DIST", "part number,price,cage\nMS24693-C274,14.00,96906\n")["loaded"] == 1
    db.commit()
    assert db.query(CatalogPrice).filter(CatalogPrice.organization_id == org_id).count() == 3

    # A line whose approved part has a catalog price: the provisional cost comes from the catalog tier.
    rec = db.execute(select(Recommendation).where(Recommendation.organization_id == org_id,
                                                  Recommendation.provisional.is_(True),
                                                  Recommendation.recommended_bid.is_not(None))).scalars().first()
    line = db.get(SolicitationLine, rec.solicitation_line_id)
    db.query(ApprovedSource).filter(ApprovedSource.nsn == line.nsn).delete()
    db.add(ApprovedSource(nsn=line.nsn, cage_code="80205", part_number="NAS1352-4-8"))
    db.commit()
    rec = refresh_line_for_org(db, org_id, line)
    db.commit()
    est = rec.explanation["cost_estimate"]
    assert est["tier"] == "catalog" and est["n"] == 1 and est["median"] == pytest.approx(0.84)
    assert rec.landed_breakdown["supplier_unit_price"] == pytest.approx(0.84) and "catalog" in rec.landed_breakdown["basis"]
    clear_item_index()
    assert build_item_index(db) is not None  # the index loads characteristics (none here) without error
