"""Derived award prices are only trusted when verified prices back them up."""

import pandas as pd

from app.services.data_quality import filter_awards


def test_derived_prices_are_quarantined_unless_consistent_with_verified_history():
    df = pd.DataFrame([
        {"nsn": "A", "unit_price": 10.0, "source": "rfq_pdf_history"},
        {"nsn": "A", "unit_price": 12.0, "source": "award_pdf"},
        {"nsn": "A", "unit_price": 11.5, "source": "award_listing"},     # consistent: kept
        {"nsn": "A", "unit_price": 20588.0, "source": "award_listing"},  # total / wrong quantity: quarantined
        {"nsn": "B", "unit_price": 3.0, "source": "record_page"},       # nothing to verify against: quarantined
    ])
    kept, counts = filter_awards(df)
    assert sorted(kept["unit_price"].tolist()) == [10.0, 11.5, 12.0]
    assert counts == {"verified": 2, "derived_kept": 1, "derived_quarantined": 2}
