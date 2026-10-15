"""Award price quality: which unit prices can be trusted for training, evaluation and the track record.

Exact unit prices come from RFQ-PDF procurement-history tables and award PDFs. Award listings and record pages
only give a contract total; dividing it by the solicitation quantity is wrong whenever DLA awarded a different
quantity or the total covers several items (live data had hex cap screws "at" $20,588 each). Such derived prices
are used only when they agree, within MAX_RATIO, with the NSN's verified prices; otherwise they are quarantined.
"""

from __future__ import annotations

import pandas as pd

RELIABLE_SOURCES = ("rfq_pdf_history", "award_pdf", "synthetic")
DERIVED_SOURCES = ("award_listing", "record_page")
MAX_RATIO = 5.0


def filter_awards(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Keep verified rows and derived rows consistent with the NSN's verified median. Needs columns
    nsn, unit_price, source. Returns (kept rows, counts)."""
    if df.empty or "source" not in df:
        return df, {"verified": len(df), "derived_kept": 0, "derived_quarantined": 0}
    reliable = df[df["source"].isin(RELIABLE_SOURCES)]
    derived = df[~df["source"].isin(RELIABLE_SOURCES)]
    med = reliable.groupby("nsn")["unit_price"].median()
    ref = derived["nsn"].map(med)
    ratio = derived["unit_price"] / ref
    ok = ref.notna() & ratio.between(1 / MAX_RATIO, MAX_RATIO)
    kept = pd.concat([reliable, derived[ok]]).sort_index()
    return kept, {"verified": int(len(reliable)), "derived_kept": int(ok.sum()),
                  "derived_quarantined": int((~ok).sum())}
