"""No feature may use information from on/after the row's as-of date.

Strategy: build the training frame, then for sampled rows corrupt EVERY award dated on or after the
row's as_of (prices x 7, quantities x 3, new awardees) and add extra future awards. Recomputing the
row's features must give identical values. If any feature peeked at the future, this fails.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from dibbs_ml.features import (
    FEATURES,
    FscMedianIndex,
    PriorAwards,
    build_training_frame,
    compute_features,
    row_as_of,
)
from dibbs_ml.inflation import default_ppi
from dibbs_ml.synthetic import generate


@pytest.fixture(scope="module")
def data():
    return generate(n_nsns=60, start=date(2023, 1, 1), end=date(2025, 12, 31), n_open_lines=8, seed=3)


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b, rel=1e-12, abs=1e-12)
    return a == b


def _features_for(awards: pd.DataFrame, rec, ppi):
    g = awards[awards["nsn"] == rec.nsn]
    as_of = row_as_of(rec.issue_date, rec.award_date)
    ctx = {c: getattr(rec, c) for c in ("fsc", "unit_of_issue", "buyer_code", "amsc", "type_code", "set_aside",
                                        "inspection_point", "fob", "approved_source_only", "n_approved_sources",
                                        "fast_award", "aidc", "first_article", "dod_standard_price")}
    fmed = FscMedianIndex(awards).get(rec.fsc, as_of)
    feats, ref, ref_type = compute_features(PriorAwards.from_frame(g), ctx, rec.quantity, as_of, ppi, fmed)
    return feats, ref, ref_type, as_of


def test_features_ignore_future_awards(data):
    ppi = default_ppi()
    awards = data.awards
    rng = np.random.default_rng(0)
    sample = awards.iloc[rng.choice(len(awards), size=40, replace=False)]
    for rec in sample.itertuples(index=False):
        feats, ref, ref_type, as_of = _features_for(awards, rec, ppi)

        future = pd.to_datetime(awards["award_date"]).dt.date >= as_of
        corrupted = awards.copy()
        corrupted.loc[future, "unit_price"] *= 7.0
        corrupted.loc[future, "quantity"] *= 3.0
        corrupted.loc[future, "awardee_cage"] = "ZZZZZ"
        extra = awards[awards["nsn"] == rec.nsn].head(3).copy()
        extra["award_date"] = as_of + timedelta(days=1)
        extra["unit_price"] = 9999.0
        extra["contract_number"] = extra["contract_number"] + "X"
        corrupted = pd.concat([corrupted, extra], ignore_index=True)

        feats2, ref2, ref_type2, _ = _features_for(corrupted, rec, ppi)
        assert ref == pytest.approx(ref2) and ref_type == ref_type2
        for f in FEATURES:
            assert _same(feats[f], feats2[f]), f"feature {f} changed when future awards changed"


def test_training_frame_uses_only_strictly_prior_awards(data):
    frame = build_training_frame(data.awards, default_ppi())
    assert len(frame) > 100
    assert (pd.to_datetime(frame["as_of"]) < pd.to_datetime(frame["award_date"])).all()
    warm = frame[~frame["is_cold"]]
    # days since last prior award is measured from as_of and must be positive (strictly prior).
    assert (warm["days_since_last_award"] > 0).all()
    # The target row's own award never serves as its reference price.
    awards = data.awards.copy()
    awards["award_date"] = pd.to_datetime(awards["award_date"]).dt.date
    for rec in warm.sample(30, random_state=1).itertuples(index=False):
        prior = awards[(awards["nsn"] == rec.nsn) & (awards["award_date"] < rec.as_of)]
        assert len(prior) == rec.n_prior_awards


def test_fsc_median_is_strictly_prior():
    awards = pd.DataFrame(
        {
            "fsc": ["5305"] * 4,
            "award_date": [date(2024, 1, 5), date(2024, 2, 10), date(2024, 3, 1), date(2024, 3, 20)],
            "unit_price": [1.0, 2.0, 100.0, 1000.0],
        }
    )
    idx = FscMedianIndex(awards)
    # As of mid-March only Jan + Feb awards (before 2024-03-01) count.
    assert idx.get("5305", date(2024, 3, 25)) == pytest.approx(1.5)
