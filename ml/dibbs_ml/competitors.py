"""Competitors: who wins a part, how far below the last price they win, and whether a broker can win it at all.

DIBBS publishes the winner's CAGE and price for every award, so for each supply class we can profile every repeat
winner: how often they win, which parts, how recently, and the ratio of their winning price to the inflation-adjusted
previous award (how far they undercut the last price). Winners are classified as manufacturers (an approved source
for some NSN, or DLA names them as the manufacturer) or resellers (brokers and distributors).

Two outputs:

* **Contestability**: P(a broker can win this line) = the share of the part's awards won by resellers, shrunk toward
  the supply class's reseller share when the part has few awards. A part the manufacturer always wins is not
  broker-winnable, whatever the price model says.
* **Competitor view of P(win)**: P(bid < every likely competitor's price) = prod_i (1 - p_i F_i(bid)), with p_i the
  chance competitor i bids (incumbents by their share of the part's awards, supply-class players by a fraction of
  theirs) and F_i their win-price distribution around the reference. Only winning prices are observed, never losing
  bids, so F_i is biased low and this view is shown next to the calibrated price model, never used in place of it.

Pure Python over a DataFrame of awards; the backend builds and caches one `FscCompetitors` per supply class.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

WINDOW_MONTHS = 36
SHRINK_K = 3.0  # awards' worth of weight given to the FSC reseller share when a part has few awards
DEFAULT_RESELLER_SHARE = 0.70  # before any awards in the supply class are known
P_WINNABLE_FLOOR = 0.05
TOP_PLAYERS = 8
FSC_PLAYER_PARTICIPATION = 0.5  # a supply-class player bids on about this fraction of the lines it does not already hold
RATIO_SIGMA_FLOOR = 0.15
MIN_RATIO_OBS = 3
MIN_BAND_OBS = 30  # awards needed before the class's automated price band is trusted


@dataclass
class CompetitorProfile:
    cage: str
    wins: int
    nsns: int
    last_win: date | None
    is_manufacturer: bool
    share: float  # of the awards considered (the part's, or the supply class's)
    median_ratio: float | None  # winning price / inflation-adjusted previous award (None: fewer than MIN_RATIO_OBS)
    ratio_sigma: float | None
    n_ratio: int = 0
    name: str | None = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["last_win"] = self.last_win.isoformat() if self.last_win else None
        for k in ("share", "median_ratio", "ratio_sigma"):
            if d[k] is not None:
                d[k] = round(d[k], 3)
        return d


@dataclass
class LineCompetitors:
    nsn: str
    fsc: str
    n_awards: int
    incumbents: list[CompetitorProfile]
    fsc_players: list[CompetitorProfile]
    reseller_share_nsn: float | None
    reseller_share_fsc: float
    p_winnable: float
    reason: str
    reseller_wins_nsn: int = 0

    def to_dict(self) -> dict:
        return {
            "nsn": self.nsn, "fsc": self.fsc, "n_awards": self.n_awards,
            "incumbents": [c.to_dict() for c in self.incumbents],
            "fsc_players": [c.to_dict() for c in self.fsc_players],
            "reseller_share_nsn": None if self.reseller_share_nsn is None else round(self.reseller_share_nsn, 3),
            "reseller_share_fsc": round(self.reseller_share_fsc, 3),
            "reseller_wins_nsn": self.reseller_wins_nsn,
            "p_winnable": round(self.p_winnable, 3), "reason": self.reason,
        }

    def p_win(self, bid: float, reference: float | None, pooled_mu: float = 0.0, pooled_sigma: float = 0.25) -> float | None:
        """Competitor view of the chance a bid wins: nobody likely to bid comes in under it."""
        if not reference or reference <= 0 or bid <= 0:
            return None
        x = math.log(bid / reference)
        p_none = 1.0
        for c, participation in self._bidders():
            mu = math.log(c.median_ratio) if c.median_ratio else pooled_mu
            sigma = c.ratio_sigma if c.ratio_sigma else pooled_sigma
            p_none *= 1.0 - participation * float(norm.cdf((x - mu) / max(sigma, RATIO_SIGMA_FLOOR)))
        return p_none

    def _bidders(self):
        for c in self.incumbents:
            yield c, min(c.share, 0.95)
        for c in self.fsc_players:
            yield c, min(c.share * FSC_PLAYER_PARTICIPATION, 0.5)


@dataclass
class FscCompetitors:
    """Everything about one supply class's winners, built from its awards with CAGE codes."""

    fsc: str
    awards: pd.DataFrame  # nsn, award_date, cage, unit_price, quantity, ratio (NaN when no prior award), is_oem
    profiles: dict[str, CompetitorProfile] = field(default_factory=dict)
    reseller_share: float = DEFAULT_RESELLER_SHARE
    pooled_mu: float = 0.0
    pooled_sigma: float = 0.25
    names: dict[str, str] = field(default_factory=dict)
    # The band within which DLA's automated evaluation has actually awarded: the 95th percentile of winning price /
    # inflation-adjusted previous award on automated (T/U) solicitations in this class. None with too few awards.
    automated_ratio_p95: float | None = None
    n_automated_ratios: int = 0

    @classmethod
    def build(cls, fsc: str, awards: pd.DataFrame, oem_cages: set[str], ppi=None, as_of: date | None = None,
              names: dict[str, str] | None = None) -> FscCompetitors:
        """`awards` columns: nsn, award_date, awardee_cage, unit_price, quantity, optionally solicitation_number
        (one supply class, verified prices)."""
        as_of = as_of or date.today()
        since = as_of - timedelta(days=int(WINDOW_MONTHS * 30.4))
        df = awards.copy()
        if df.empty:
            return cls(fsc, df.assign(cage=[], ratio=[], is_oem=[]), names=names or {})
        df["award_date"] = pd.to_datetime(df["award_date"]).dt.date
        df["cage"] = df["awardee_cage"].astype(str).str.upper()
        df = df[(df["unit_price"] > 0) & df["awardee_cage"].notna()].sort_values(["nsn", "award_date"]).reset_index(drop=True)
        # Ratio to the inflation-adjusted previous award of the same NSN (NaN for a part's first award).
        ratios = np.full(len(df), np.nan)
        for _, idx in df.groupby("nsn").indices.items():
            idx = np.asarray(idx)
            for j in range(1, idx.size):
                prev, cur = df.iloc[idx[j - 1]], df.iloc[idx[j]]
                factor = ppi.factor(fsc, prev["award_date"], cur["award_date"]) if ppi is not None else 1.0
                ratios[idx[j]] = cur["unit_price"] / (prev["unit_price"] * factor)
        df["ratio"] = ratios
        oem = {c.upper() for c in oem_cages}
        df["is_oem"] = df["cage"].isin(oem)
        recent = df[df["award_date"] >= since]
        base = recent if len(recent) >= 20 else df
        out = cls(fsc, df, names=names or {})
        n = len(base)
        out.reseller_share = float((~base["is_oem"]).mean()) if n else DEFAULT_RESELLER_SHARE
        lr = np.log(base["ratio"].dropna().to_numpy(float)) if n else np.array([])
        if lr.size >= MIN_RATIO_OBS:
            out.pooled_mu, out.pooled_sigma = float(np.median(lr)), max(float(np.std(lr, ddof=1)), RATIO_SIGMA_FLOOR)
        # Automated price band: what DLA's price-reasonableness logic has let through on automated solicitations.
        if "solicitation_number" in df:
            kind = df["solicitation_number"].astype(str).str.upper().str[8]
            auto = df[kind.isin(["T", "U"])]["ratio"].dropna().to_numpy(float)
        else:
            auto = np.array([])
        if auto.size < MIN_BAND_OBS:
            auto = df["ratio"].dropna().to_numpy(float)  # too few typed awards: every award in the class
        if auto.size >= MIN_BAND_OBS:
            out.automated_ratio_p95 = float(np.quantile(auto, 0.95))
            out.n_automated_ratios = int(auto.size)
        for cage, g in base.groupby("cage"):
            out.profiles[cage] = out._profile(cage, g, n)
        return out

    def _profile(self, cage: str, g: pd.DataFrame, denominator: int) -> CompetitorProfile:
        r = np.log(g["ratio"].dropna().to_numpy(float))
        med = float(math.exp(np.median(r))) if r.size >= MIN_RATIO_OBS else None
        sig = max(float(np.std(r, ddof=1)), RATIO_SIGMA_FLOOR) if r.size >= MIN_RATIO_OBS else None
        return CompetitorProfile(
            cage=cage, wins=int(len(g)), nsns=int(g["nsn"].nunique()), last_win=max(g["award_date"]),
            is_manufacturer=bool(g["is_oem"].iloc[0]), share=len(g) / denominator if denominator else 0.0,
            median_ratio=med, ratio_sigma=sig, n_ratio=int(r.size), name=self.names.get(cage),
        )

    def top_players(self, k: int = TOP_PLAYERS, exclude: set[str] | None = None) -> list[CompetitorProfile]:
        ex = exclude or set()
        return sorted((p for c, p in self.profiles.items() if c not in ex), key=lambda p: (-p.wins, p.cage))[:k]

    def for_line(self, nsn: str, org_cage: str | None = None, as_of: date | None = None) -> LineCompetitors:
        """Incumbents and likely supply-class bidders for one part, plus contestability."""
        as_of = as_of or date.today()
        g = self.awards[(self.awards["nsn"] == nsn) & (self.awards["award_date"] < as_of)] if len(self.awards) else self.awards
        n = len(g)
        incumbents: list[CompetitorProfile] = []
        reseller_wins = 0
        own = (org_cage or "").upper()
        if n:
            for cage, gg in g.groupby("cage"):
                p = self._profile(cage, gg, n)
                # The broker's own past wins count as reseller wins (it is one), whatever DLA calls it.
                if cage == own:
                    p.is_manufacturer = False
                reseller_wins += 0 if p.is_manufacturer else int(len(gg))
                incumbents.append(p)
            incumbents.sort(key=lambda p: (-p.wins, p.cage))
        share_nsn = reseller_wins / n if n else None
        # Shrink the part's reseller share toward the supply class's: three awards' worth of prior.
        p_win = ((reseller_wins + SHRINK_K * self.reseller_share) / (n + SHRINK_K)) if n else self.reseller_share
        p_win = min(max(p_win, P_WINNABLE_FLOOR), 1.0)
        if n == 0:
            reason = f"no award history for this part; resellers win {100 * self.reseller_share:.0f}% of FSC {self.fsc} awards"
        elif reseller_wins == 0:
            top = incumbents[0]
            reason = (f"the manufacturer ({top.cage}) won all {n} award{'s' if n > 1 else ''}: a reseller rarely beats its price"
                      if top.is_manufacturer else f"manufacturers won all {n} awards for this part")
        elif reseller_wins == n:
            reason = f"resellers won all {n} award{'s' if n > 1 else ''} for this part"
        else:
            reason = f"resellers won {reseller_wins} of {n} awards for this part (FSC {self.fsc}: {100 * self.reseller_share:.0f}%)"
        players = self.top_players(exclude={p.cage for p in incumbents} | ({own} if own else set()))
        return LineCompetitors(nsn, self.fsc, n, incumbents, players, share_nsn, self.reseller_share, p_win, reason,
                               reseller_wins_nsn=reseller_wins)


def evaluate_contestability(awards: pd.DataFrame, oem_cages: set[str], fsc: str, min_prior: int = 1) -> dict:
    """Walk a supply class's awards in time order and score P(a reseller wins the next award) from the prior ones.

    Returns Brier score and calibration bins for the shrunk reseller share against the constant FSC share.
    """
    comp = FscCompetitors.build(fsc, awards, oem_cages)
    df = comp.awards.sort_values("award_date")
    preds, base, obs = [], [], []
    for row in df.itertuples(index=False):
        prior = comp.awards[(comp.awards["nsn"] == row.nsn) & (comp.awards["award_date"] < row.award_date)]
        if len(prior) < min_prior:
            continue
        rw = int((~prior["is_oem"]).sum())
        preds.append(min(max((rw + SHRINK_K * comp.reseller_share) / (len(prior) + SHRINK_K), P_WINNABLE_FLOOR), 1.0))
        base.append(comp.reseller_share)
        obs.append(0.0 if row.is_oem else 1.0)
    p, b, y = np.array(preds), np.array(base), np.array(obs)
    if not p.size:
        return {"n": 0}
    bins = []
    for lo in np.arange(0, 1, 0.2):
        m = (p >= lo) & (p < lo + 0.2 + (1e-9 if lo >= 0.8 else 0))
        if m.any():
            bins.append({"bin": f"{lo:.1f}-{lo + 0.2:.1f}", "predicted": float(p[m].mean()), "observed": float(y[m].mean()), "n": int(m.sum())})
    return {"n": int(p.size), "brier": float(np.mean((p - y) ** 2)), "baseline_brier": float(np.mean((b - y) ** 2)),
            "reseller_share": float(y.mean()), "bins": bins}
