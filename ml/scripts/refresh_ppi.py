"""Refresh ml/data/ppi.csv: monthly BLS Producer Price Index series used for inflation adjustment.

Source: the public FRED CSV endpoint (mirrors BLS PPI by commodity; no API key needed).
    python ml/scripts/refresh_ppi.py [--start 2015-01-01]

Series and the FSCs they deflate are defined in dibbs_ml/inflation.py (FSC_SERIES).
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import httpx

ML_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_ROOT))

from dibbs_ml.inflation import ALL_SERIES, PPI_CSV  # noqa: E402

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
UA = "DIBBSBidCopilot/0.1 (+mailto:ops@copilot.example.com)"


def fetch(series_id: str, start: str, client: httpx.Client) -> list[tuple[str, float]]:
    r = client.get(FRED.format(sid=series_id, start=start))
    r.raise_for_status()
    rows = []
    for rec in csv.DictReader(io.StringIO(r.text)):
        val = rec.get(series_id, ".")
        if val in (".", ""):
            continue
        rows.append((rec["observation_date"], float(val)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    args = ap.parse_args()
    out: list[tuple[str, str, float]] = []
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as client:
        for sid in ALL_SERIES:
            rows = fetch(sid, args.start, client)
            print(f"{sid}: {len(rows)} months ({rows[0][0]} .. {rows[-1][0]})")
            out.extend((d, sid, v) for d, v in rows)
            time.sleep(1.0)
    PPI_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PPI_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "series_id", "value"])
        w.writerows(sorted(out))
    print(f"wrote {PPI_CSV}")


if __name__ == "__main__":
    main()
