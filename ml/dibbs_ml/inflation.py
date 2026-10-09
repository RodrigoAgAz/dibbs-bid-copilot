"""Inflation adjustment with BLS Producer Price Index series (static CSV in ml/data/ppi.csv)."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

PPI_CSV = Path(__file__).resolve().parents[1] / "data" / "ppi.csv"

# FSC -> PPI commodity series (FRED ids mirror BLS PPI by commodity).
FSC_SERIES: dict[str, str] = {
    "5305": "WPU1081",  # Bolts, nuts, screws, rivets and washers
    "5306": "WPU1081",
    "5310": "WPU1081",
    "5340": "WPU10",  # Metals and metal products (hardware is broad)
    "4730": "WPU10",  # Fittings: broad metals index
    "5330": "WPU0713",  # Miscellaneous rubber products (packing, gaskets, o-rings)
    "6145": "WPU1026",  # Nonferrous wire and cable
}
DEFAULT_SERIES = "WPU10"
ALL_SERIES = sorted(set(FSC_SERIES.values()) | {DEFAULT_SERIES})


class PPI:
    def __init__(self, df: pd.DataFrame) -> None:
        self._series: dict[str, tuple[list[date], list[float]]] = {}
        for sid, g in df.groupby("series_id"):
            g = g.sort_values("date")
            self._series[str(sid)] = ([d for d in g["date"]], [float(v) for v in g["value"]])

    @classmethod
    def load(cls, path: Path | None = None) -> PPI:
        p = path or PPI_CSV
        if not p.exists():
            return cls(pd.DataFrame({"date": [], "series_id": [], "value": []}))
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return cls(df)

    def series_for(self, fsc: str | None) -> str:
        sid = FSC_SERIES.get(str(fsc or ""), DEFAULT_SERIES)
        return sid if sid in self._series else DEFAULT_SERIES

    def index(self, fsc: str | None, d: date) -> float | None:
        """Index value for the month containing `d` (last known value if `d` is beyond the data)."""
        sid = self.series_for(fsc)
        if sid not in self._series:
            return None
        dates, vals = self._series[sid]
        i = bisect_right(dates, d) - 1
        if i < 0:
            return vals[0]
        return vals[i]

    def factor(self, fsc: str | None, from_date: date, to_date: date) -> float:
        """Multiply a price observed at `from_date` by this to express it in `to_date` dollars."""
        a, b = self.index(fsc, from_date), self.index(fsc, to_date)
        if not a or not b:
            return 1.0
        return b / a


@lru_cache(maxsize=1)
def default_ppi() -> PPI:
    return PPI.load()
