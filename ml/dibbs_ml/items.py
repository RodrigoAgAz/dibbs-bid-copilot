"""Similar items: price a part by what DLA paid for comparable parts.

An NSN with no award history still has a description: its supply class (FSC), item name ("noun, modifiers" in
DLA's naming convention), unit of issue, approved-source manufacturers (CAGE codes) and their part numbers. Parts
from the same manufacturer, in the same part-number family, or with the same name and modifiers sell at related
prices. `ItemIndex` finds the most similar NSNs (static metadata only, so no leakage), and
`similar_item_features` turns THEIR award history strictly before `as_of`, adjusted for inflation and quantity,
into a reference price and evidence features for the cold-start model.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from dibbs_ml.features import SIM_FEATURE_NAMES, PriorAwards
from dibbs_ml.inflation import PPI

# Within-NSN elasticity of unit price to quantity, estimated on real DIBBS awards (research/lab.py: -0.14).
QTY_ELASTICITY = -0.14
NEIGHBORS = 25
MIN_SCORE = 0.25

_TOKEN = re.compile(r"[A-Z0-9]+")
_STOP = {"AND", "OR", "OF", "FOR", "THE", "W", "WITH", "ASSY", "ASSEMBLY"}


def noun(nomenclature: str | None) -> str:
    """DLA item names are "NOUN, MODIFIER ..." (sometimes without the space): the noun is the first segment."""
    s = (nomenclature or "").upper().strip()
    return s.split(",")[0].strip() or "?"


def modifier_tokens(nomenclature: str | None) -> set[str]:
    parts = (nomenclature or "").upper().split(",", 1)
    rest = parts[1] if len(parts) > 1 else ""
    return {t for t in _TOKEN.findall(rest) if len(t) > 1 and t not in _STOP}


def pn_family(cage: str | None, part_number: str | None) -> str | None:
    """Manufacturer part-number family: CAGE + the part number up to its last separator ("30781:082-68335")."""
    pn = re.sub(r"\s.*$", "", (part_number or "").upper().strip())  # drop trailing "REV H ITEM 9" style notes
    if not pn or not cage:
        return None
    cut = max(pn.rfind("-"), pn.rfind("/"))
    stem = pn[:cut] if cut >= 3 else re.sub(r"\d{1,3}[A-Z]?$", "", pn)
    return f"{cage}:{stem}" if len(stem) >= 3 else None


def niin_country(nsn: str) -> str:
    """NIIN positions 1-2: NATO country code of the cataloguing country (00/01 = US)."""
    return nsn[4:6] if len(nsn) >= 6 else "NA"


@dataclass
class Item:
    nsn: str
    fsc: str
    noun: str
    mods: frozenset[str]
    unit: str
    cages: frozenset[str]
    families: frozenset[str]


@dataclass
class ItemIndex:
    """Static descriptions of NSNs; neighbors are computed lazily per (FSC, noun) group and cached."""

    items: dict[str, Item] = field(default_factory=dict)
    by_group: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    by_fsc: dict[str, list[str]] = field(default_factory=dict)
    by_cage: dict[str, list[str]] = field(default_factory=dict)
    by_family: dict[str, list[str]] = field(default_factory=dict)
    _cache: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)

    @classmethod
    def from_frames(cls, lines: pd.DataFrame, sources: pd.DataFrame | None = None) -> ItemIndex:
        """lines: nsn, nomenclature, unit_of_issue (any number of rows per NSN; the last non-empty wins).
        sources: nsn, cage_code, part_number (approved sources)."""
        idx = cls()
        meta: dict[str, dict] = {}
        for r in lines.itertuples(index=False):
            m = meta.setdefault(r.nsn, {"nom": None, "unit": None})
            if getattr(r, "nomenclature", None):
                m["nom"] = r.nomenclature
            if getattr(r, "unit_of_issue", None):
                m["unit"] = r.unit_of_issue
        cages: dict[str, set[str]] = defaultdict(set)
        fams: dict[str, set[str]] = defaultdict(set)
        if sources is not None and len(sources):
            for r in sources.itertuples(index=False):
                if r.cage_code:
                    cages[r.nsn].add(str(r.cage_code).upper())
                    f = pn_family(str(r.cage_code).upper(), r.part_number)
                    if f:
                        fams[r.nsn].add(f)
        for nsn, m in meta.items():
            if not m["nom"] or len(nsn) != 13:
                continue
            it = Item(nsn=nsn, fsc=nsn[:4], noun=noun(m["nom"]), mods=frozenset(modifier_tokens(m["nom"])),
                      unit=(m["unit"] or "").upper(), cages=frozenset(cages.get(nsn, ())),
                      families=frozenset(fams.get(nsn, ())))
            idx.items[nsn] = it
            idx.by_group.setdefault((it.fsc, it.noun), []).append(nsn)
            idx.by_fsc.setdefault(it.fsc, []).append(nsn)
            for c in it.cages:
                idx.by_cage.setdefault(f"{it.fsc}:{c}", []).append(nsn)
            for f in it.families:
                idx.by_family.setdefault(f, []).append(nsn)
        return idx

    def __len__(self) -> int:
        return len(self.items)

    @staticmethod
    def score(a: Item, b: Item) -> tuple[float, str]:
        """Similarity in [0, 1] and the strongest reason."""
        s, why = 0.0, "same supply class"
        if a.noun == b.noun:
            s += 0.35
            why = "same item name"
        if a.mods or b.mods:
            j = len(a.mods & b.mods) / len(a.mods | b.mods)
            s += 0.2 * j
            if j >= 0.5 and a.noun == b.noun:
                why = "same name and modifiers"
        if a.unit and a.unit == b.unit:
            s += 0.05
        if a.cages & b.cages:
            s += 0.2
            why = "same manufacturer"
        if a.families & b.families:
            s += 0.2
            why = "same part-number family"
        return min(s, 1.0), why

    def neighbors(self, nsn: str, k: int = NEIGHBORS) -> list[tuple[str, float, str]]:
        """Most similar other NSNs: (nsn, score, reason). Candidates: same-FSC items in the same part-number
        family, the only kind of similarity with measured price signal (see SIM_FEATURES); name or
        manufacturer matches alone would mean thousands of noisy candidates for names like GASKET."""
        if nsn in self._cache:
            return self._cache[nsn]
        a = self.items.get(nsn)
        if a is None:
            return []
        cands: set[str] = set()
        for f in a.families:
            cands.update(o for o in self.by_family.get(f, ()) if o[:4] == a.fsc)
        cands.discard(nsn)
        scored = []
        for other in cands:
            s, why = self.score(a, self.items[other])
            if s >= MIN_SCORE:
                scored.append((other, s, why))
        scored.sort(key=lambda t: (-t[1], t[0]))
        self._cache[nsn] = scored[:k]
        return self._cache[nsn]


# Measured on held-out NSNs (research/lab.py, research/README.md): part-number-family neighbors carry real price
# signal (median error 49% vs 329% for the FSC median on those new parts); same-manufacturer and same-name
# neighbors do not (93% vs 88%), so only part-family evidence is used.
SIM_FEATURES = SIM_FEATURE_NAMES  # defined with the other feature names in dibbs_ml.features


def _wmedian(logs: list[float], weights: list[float]) -> float:
    lp, w = np.array(logs), np.array(weights)
    o = np.argsort(lp)
    cw = np.cumsum(w[o]) / w.sum()
    return float(lp[o][np.searchsorted(cw, 0.5)])


def similar_item_features(
    neighbors: list[tuple[str, float, str]],
    histories: Mapping[str, PriorAwards],
    quantity: float,
    as_of: date,
    ppi: PPI,
    fsc: str,
    fsc_median: float | None,
    items: ItemIndex | None = None,
    nsn: str | None = None,
) -> tuple[dict, float | None, list[dict]]:
    """Features + reference price from same-part-family NSNs' awards strictly before `as_of`.

    Each family member contributes its most recent prior award, inflation-adjusted to `as_of` and moved to the
    target quantity with QTY_ELASTICITY. Reference: their similarity-weighted median (log space), else None.
    Returns (features, reference or None, evidence rows for the UI).
    """
    q = max(float(quantity or 1.0), 1e-9)
    me = items.items.get(nsn) if items is not None and nsn else None
    logs: list[float] = []
    weights: list[float] = []
    evidence = []
    for other, s, why in neighbors:
        h = histories.get(other)
        if h is None:
            continue
        o_item = items.items.get(other) if items is not None else None
        fam = why == "same part-number family" or (
            me is not None and o_item is not None and bool(me.families & o_item.families))
        if not fam:
            continue
        p = h.before(as_of)
        if not len(p):
            continue
        d = p.dates[-1].astype("datetime64[D]").astype(object)
        price = float(p.prices[-1]) * ppi.factor(fsc, d, as_of)
        qi = max(float(p.qtys[-1]), 1e-9)
        adj = price * (q / qi) ** QTY_ELASTICITY
        logs.append(math.log(max(adj, 1e-12)))
        weights.append(s)
        if len(evidence) < 8:
            evidence.append({"nsn": other, "score": round(s, 2), "reason": "same part-number family",
                             "award_date": str(d), "unit_price": round(float(p.prices[-1]), 4), "quantity": qi,
                             "adjusted_price": round(adj, 4)})
    feats = {k: float("nan") for k in SIM_FEATURES}
    feats["fam_n"] = float(len(logs))
    if not logs:
        return feats, None, []
    lf = _wmedian(logs, weights)
    if len(logs) >= 2:
        feats["fam_spread"] = float(np.ptp(logs))
    if fsc_median and fsc_median > 0:
        feats["fam_log_ref_vs_fsc"] = lf - math.log(fsc_median)
    return feats, math.exp(lf), evidence


def histories_for(awards: pd.DataFrame, nsns: Iterable[str] | None = None) -> dict[str, PriorAwards]:
    df = awards[(awards["unit_price"] > 0) & (awards["quantity"] > 0)]
    if nsns is not None:
        df = df[df["nsn"].isin(set(nsns))]
    return {n: PriorAwards.from_frame(g) for n, g in df.groupby("nsn")}
