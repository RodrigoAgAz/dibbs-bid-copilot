"""Leakage-safe feature construction, shared by training and inference.

One row per historical award line. Every feature is computed from awards that STRICTLY precede
the row's `as_of` date (the solicitation's issue date; if unknown, award_date - AS_OF_LAG_DAYS),
plus solicitation context known when the RFQ is posted. `compute_features` filters its input by
date itself, so callers cannot leak future awards by accident; `tests/test_leakage.py` checks it.

Target: y = log(unit_price / reference_price), where reference_price is the inflation-adjusted
most recent prior award price for the NSN, else the DoD standard price, else the FSC median.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dibbs_ml.inflation import PPI

AS_OF_LAG_DAYS = 21  # assumed RFQ-issue-to-award lag when the solicitation date is unknown
FSC_MEDIAN_WINDOW_MONTHS = 36
TREND_N = 6

CAT_FEATURES = [
    "fsc",
    "unit_of_issue",
    "buyer_prefix",
    "amsc",
    "type_code",
    "set_aside",
    "inspection_point",
    "fob",
    "ref_type",
    "niin_country",  # NATO cataloguing country (00/01 = US): foreign-origin items price differently
]
# Similar-item evidence (dibbs_ml.items): filled by build_training_frame / the pricing service when an item index
# is available, NaN otherwise.
SIM_FEATURE_NAMES = ["fam_log_ref_vs_fsc", "fam_n", "fam_spread"]
NUM_FEATURES = [
    "log_quantity",
    "qty_rel_median",
    "log_qty_vs_last",
    "n_prior_awards",
    "days_since_last_award",
    "f_last3_median",
    "f_min",
    "f_max",
    "volatility",
    "trend_per_year",
    "n_distinct_awardees",
    "incumbent_share",
    "approved_source_only",
    "n_approved_sources",
    "fast_award",
    "aidc",
    "first_article",
    "log_last_to_dod",
    "inflation_change",
    "log_ref_price",
    *SIM_FEATURE_NAMES,
]
FEATURES = NUM_FEATURES + CAT_FEATURES
# Features that only exist with NSN history: the cold-start (FSC-level) model never sees them.
HISTORY_FEATURES = {
    "qty_rel_median",
    "log_qty_vs_last",
    "n_prior_awards",
    "days_since_last_award",
    "f_last3_median",
    "f_min",
    "f_max",
    "volatility",
    "trend_per_year",
    "n_distinct_awardees",
    "incumbent_share",
    "log_last_to_dod",
    "inflation_change",
}
COLD_FEATURES = [f for f in FEATURES if f not in HISTORY_FEATURES]


@dataclass
class PriorAwards:
    """Award history of one NSN, sorted by date."""

    dates: np.ndarray  # datetime64[D]
    prices: np.ndarray  # unit prices
    qtys: np.ndarray
    cages: np.ndarray  # object array (may contain None)

    @classmethod
    def empty(cls) -> PriorAwards:
        return cls(np.array([], dtype="datetime64[D]"), np.array([]), np.array([]), np.array([], dtype=object))

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> PriorAwards:
        if df is None or df.empty:
            return cls.empty()
        df = df.sort_values("award_date")
        return cls(
            pd.to_datetime(df["award_date"]).values.astype("datetime64[D]"),
            df["unit_price"].to_numpy(dtype=float),
            df["quantity"].to_numpy(dtype=float),
            df["awardee_cage"].to_numpy(dtype=object) if "awardee_cage" in df else np.full(len(df), None, dtype=object),
        )

    def before(self, as_of: date) -> PriorAwards:
        k = int(np.searchsorted(self.dates, np.datetime64(as_of, "D"), side="left"))
        return PriorAwards(self.dates[:k], self.prices[:k], self.qtys[:k], self.cages[:k])

    def __len__(self) -> int:
        return int(self.dates.size)


class FscMedianIndex:
    """Median award unit price per FSC over the 36 months before the start of `as_of`'s month.

    Month granularity keeps it cheap and strictly prior (only awards before the 1st of the month).
    """

    def __init__(self, awards: pd.DataFrame) -> None:
        self._by_fsc: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        if awards is not None and not awards.empty:
            df = awards[["fsc", "award_date", "unit_price"]].dropna()
            for fsc, g in df.groupby("fsc"):
                g = g.sort_values("award_date")
                self._by_fsc[str(fsc)] = (
                    pd.to_datetime(g["award_date"]).values.astype("datetime64[D]"),
                    g["unit_price"].to_numpy(dtype=float),
                )
        self._cache: dict[tuple[str, int, int], float | None] = {}

    def get(self, fsc: str | None, as_of: date) -> float | None:
        if not fsc or str(fsc) not in self._by_fsc:
            return None
        key = (str(fsc), as_of.year, as_of.month)
        if key not in self._cache:
            dates, prices = self._by_fsc[str(fsc)]
            end = date(as_of.year, as_of.month, 1)
            m = end.month - 1 - FSC_MEDIAN_WINDOW_MONTHS
            start = date(end.year + m // 12, m % 12 + 1, 1)
            a = np.searchsorted(dates, np.datetime64(start, "D"), side="left")
            b = np.searchsorted(dates, np.datetime64(end, "D"), side="left")
            self._cache[key] = float(np.median(prices[a:b])) if b > a else None
        return self._cache[key]


def _nan() -> float:
    return float("nan")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _cat(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NA"
    s = str(v).strip().upper()
    return s or "NA"


def _flag(v) -> float:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    return 1.0 if bool(v) else 0.0


def compute_features(
    history: PriorAwards,
    ctx: Mapping,
    quantity: float,
    as_of: date,
    ppi: PPI,
    fsc_median: float | None,
) -> tuple[dict, float, str]:
    """Features for one line as of `as_of`. Returns (features, reference_price, reference_type)."""
    prior = history.before(as_of)  # STRICTLY before as_of: the leakage guard
    fsc = str(ctx.get("fsc") or "")
    qty = max(float(quantity or 1.0), 1e-9)
    dod = _f(ctx.get("dod_standard_price"))
    n = len(prior)

    feats: dict = {k: _nan() for k in NUM_FEATURES}
    feats["log_quantity"] = math.log1p(qty)
    feats["n_prior_awards"] = float(n)

    if n > 0:
        dates = prior.dates.astype("datetime64[D]").astype(object)  # -> datetime.date
        factors = np.array([ppi.factor(fsc, d, as_of) for d in dates])
        adj = prior.prices * factors
        ref, ref_type = float(adj[-1]), "last_award"
        logp = np.log(np.clip(adj, 1e-9, None))
        feats["days_since_last_award"] = float((as_of - dates[-1]).days)
        feats["f_last3_median"] = math.log(float(np.median(adj[-3:])) / ref)
        feats["f_min"] = math.log(float(adj.min()) / ref)
        feats["f_max"] = math.log(float(adj.max()) / ref)
        if n >= 2:
            feats["volatility"] = float(np.std(logp, ddof=1))
        if n >= 3:
            t = np.array([(d - dates[-1]).days / 365.25 for d in dates[-TREND_N:]])
            y = logp[-TREND_N:]
            if np.ptp(t) > 0:
                feats["trend_per_year"] = float(np.polyfit(t, y, 1)[0])
        med_q = float(np.median(prior.qtys)) if np.all(prior.qtys > 0) else None
        if med_q:
            feats["qty_rel_median"] = qty / med_q
        if prior.qtys[-1] > 0:
            feats["log_qty_vs_last"] = math.log(qty / float(prior.qtys[-1]))
        cages = [c for c in prior.cages if c]
        if cages:
            vals, counts = np.unique(np.array(cages, dtype=str), return_counts=True)
            feats["n_distinct_awardees"] = float(vals.size)
            feats["incumbent_share"] = float(counts.max() / counts.sum())
        if dod and dod > 0:
            feats["log_last_to_dod"] = math.log(float(prior.prices[-1]) / dod)
        feats["inflation_change"] = math.log(float(factors[-1]))
    elif dod and dod > 0:
        ref, ref_type = dod, "dod_price"
    elif fsc_median and fsc_median > 0:
        ref, ref_type = float(fsc_median), "fsc_median"
    else:
        ref, ref_type = 1.0, "none"

    feats["log_ref_price"] = math.log(ref)
    feats["approved_source_only"] = _flag(ctx.get("approved_source_only"))
    nas = _f(ctx.get("n_approved_sources"))
    feats["n_approved_sources"] = nas if nas is not None else _nan()
    feats["fast_award"] = _flag(ctx.get("fast_award"))
    feats["aidc"] = _flag(ctx.get("aidc"))
    feats["first_article"] = _flag(ctx.get("first_article"))

    buyer = ctx.get("buyer_code")
    feats["fsc"] = fsc or "NA"
    feats["unit_of_issue"] = _cat(ctx.get("unit_of_issue"))
    feats["buyer_prefix"] = _cat(str(buyer)[:2]) if buyer else "NA"
    feats["amsc"] = _cat(ctx.get("amsc"))
    feats["type_code"] = _cat(ctx.get("type_code"))
    feats["set_aside"] = _cat(ctx.get("set_aside"))
    feats["inspection_point"] = _cat(ctx.get("inspection_point"))
    feats["fob"] = _cat(ctx.get("fob"))
    feats["ref_type"] = ref_type
    nsn = str(ctx.get("nsn") or "")
    feats["niin_country"] = nsn[4:6] if len(nsn) >= 6 else "NA"
    return feats, ref, ref_type


def cold_reference(ctx: Mapping, fsc_median: float | None) -> tuple[float, str]:
    """Reference price without NSN history (used by the FSC-level cold-start model)."""
    dod = _f(ctx.get("dod_standard_price"))
    if dod and dod > 0:
        return dod, "dod_price"
    if fsc_median and fsc_median > 0:
        return float(fsc_median), "fsc_median"
    return 1.0, "none"


def row_as_of(issue_date, award_date) -> date:
    if issue_date is not None and not (isinstance(issue_date, float) and math.isnan(issue_date)) and pd.notna(issue_date):
        d = pd.Timestamp(issue_date).date()
        if d <= pd.Timestamp(award_date).date():
            return d
    return pd.Timestamp(award_date).date() - timedelta(days=AS_OF_LAG_DAYS)


CONTEXT_COLUMNS = [
    "fsc",
    "unit_of_issue",
    "buyer_code",
    "amsc",
    "type_code",
    "set_aside",
    "inspection_point",
    "fob",
    "approved_source_only",
    "n_approved_sources",
    "fast_award",
    "aidc",
    "first_article",
    "dod_standard_price",
]


def build_training_frame(awards: pd.DataFrame, ppi: PPI, items=None) -> pd.DataFrame:
    """One row per award with features, target and bookkeeping columns.

    `awards` columns: nsn, fsc, award_date, unit_price, quantity, awardee_cage, and optionally
    issue_date + CONTEXT_COLUMNS (solicitation context joined by solicitation number).
    `items` (dibbs_ml.items.ItemIndex): adds similar-item evidence from OTHER NSNs' awards before each row's as_of.
    """
    df = awards.copy()
    df = df[(df["unit_price"] > 0) & (df["quantity"] > 0)].copy()
    df["award_date"] = pd.to_datetime(df["award_date"]).dt.date
    if "issue_date" not in df:
        df["issue_date"] = None
    for c in CONTEXT_COLUMNS:
        if c not in df:
            df[c] = None
    df = df.sort_values(["nsn", "award_date"]).reset_index(drop=True)
    fsc_index = FscMedianIndex(df)
    histories = None
    if items is not None:
        from dibbs_ml.items import histories_for

        histories = histories_for(df)

    rows: list[dict] = []
    for nsn, g in df.groupby("nsn", sort=False):
        hist = PriorAwards.from_frame(g)
        for rec in g.itertuples(index=False):
            as_of = row_as_of(rec.issue_date, rec.award_date)
            ctx = {c: getattr(rec, c) for c in CONTEXT_COLUMNS}
            ctx["nsn"] = nsn
            fmed = fsc_index.get(rec.fsc, as_of)
            feats, ref, ref_type = compute_features(hist, ctx, rec.quantity, as_of, ppi, fmed)
            cold_ref, cold_ref_type = cold_reference(ctx, fmed)
            sim_ref = np.nan
            if items is not None:
                from dibbs_ml.items import similar_item_features

                sim, sref, _ = similar_item_features(items.neighbors(nsn), histories, rec.quantity, as_of, ppi,
                                                     str(rec.fsc), fmed, items=items, nsn=nsn)
                feats.update(sim)
                sim_ref = sref if sref else np.nan
            prior = hist.before(as_of)
            rows.append(
                {
                    **feats,
                    "nsn": nsn,
                    "award_date": rec.award_date,
                    "as_of": as_of,
                    "unit_price": float(rec.unit_price),
                    "quantity": float(rec.quantity),
                    "awardee_cage": rec.awardee_cage,
                    "ref_price": ref,
                    "cold_ref_price": cold_ref,
                    "cold_ref_type": cold_ref_type,
                    "sim_ref_price": sim_ref,
                    "last_price_raw": float(prior.prices[-1]) if len(prior) else np.nan,
                    "is_cold": len(prior) == 0,
                    "y": math.log(float(rec.unit_price) / ref),
                    "y_cold": math.log(float(rec.unit_price) / cold_ref),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out[out["ref_type"] != "none"].reset_index(drop=True)


def similar_awards(history: PriorAwards, quantity: float, as_of: date, k: int = 5) -> list[dict]:
    """The k most similar prior awards: close in quantity (log scale) and recent."""
    prior = history.before(as_of)
    if not len(prior):
        return []
    q = max(float(quantity), 1e-9)
    ages = np.array([(as_of - d).days for d in prior.dates.astype("datetime64[D]").astype(object)], dtype=float)
    score = np.abs(np.log(np.clip(prior.qtys, 1e-9, None) / q)) + ages / 730.0
    idx = np.argsort(score)[:k]
    out = []
    for i in sorted(idx, key=lambda j: prior.dates[j], reverse=True):
        out.append(
            {
                "award_date": str(prior.dates[i]),
                "quantity": float(prior.qtys[i]),
                "unit_price": float(prior.prices[i]),
                "awardee_cage": prior.cages[i],
            }
        )
    return out


def to_model_frame(rows: pd.DataFrame | list[dict], features: list[str], categories: dict[str, list[str]]) -> pd.DataFrame:
    """Typed matrix for LightGBM: numeric floats + pandas categoricals with fixed categories."""
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    X = pd.DataFrame(index=df.index)
    for f in features:
        if f in CAT_FEATURES:
            cats = categories.get(f, [])
            X[f] = pd.Categorical(df[f].astype(str).where(df[f].astype(str).isin(cats), "NA"), categories=cats)
        else:
            X[f] = pd.to_numeric(df[f], errors="coerce").astype(float)
    return X
