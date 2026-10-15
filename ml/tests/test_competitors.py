"""Competitor profiles, reseller-share contestability and the competitor view of P(win)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from dibbs_ml.competitors import (
    DEFAULT_RESELLER_SHARE,
    P_WINNABLE_FLOOR,
    SHRINK_K,
    FscCompetitors,
    evaluate_contestability,
)

TODAY = date(2026, 10, 15)


def awards(rows):
    return pd.DataFrame(rows, columns=["nsn", "award_date", "awardee_cage", "unit_price", "quantity"])


def test_empty_supply_class_falls_back_to_defaults():
    comp = FscCompetitors.build("5305", awards([]), set(), as_of=TODAY)
    lc = comp.for_line("5305000000001", as_of=TODAY)
    assert lc.n_awards == 0 and lc.p_winnable == DEFAULT_RESELLER_SHARE and "no award history" in lc.reason
    assert lc.p_win(1.0, 2.0) == 1.0  # nobody known to bid


def test_profiles_ratios_and_reseller_share():
    rows = []
    d = TODAY - timedelta(days=400)
    # Part A: OEM 1OEM1 wins 4 times, undercutting the last price by ~5% each time; reseller 2RES2 wins once.
    price = 10.0
    for i in range(4):
        rows.append(("5305000000001", d + timedelta(days=60 * i), "1OEM1", price, 100))
        price *= 0.95
    rows.append(("5305000000001", d + timedelta(days=300), "2RES2", price * 0.9, 100))
    # Part B: resellers only.
    for i, cage in enumerate(["2RES2", "3RES3", "2RES2"]):
        rows.append(("5305000000002", d + timedelta(days=50 * i), cage, 5.0, 10))
    comp = FscCompetitors.build("5305", awards(rows), {"1OEM1"}, as_of=TODAY)
    assert comp.reseller_share == pytest.approx(4 / 8)
    oem = comp.profiles["1OEM1"]
    assert oem.wins == 4 and oem.is_manufacturer and oem.nsns == 1 and oem.median_ratio == pytest.approx(0.95, rel=1e-3)
    res = comp.profiles["2RES2"]
    assert res.wins == 3 and not res.is_manufacturer and res.nsns == 2
    lc = comp.for_line("5305000000001", as_of=TODAY)
    assert [c.cage for c in lc.incumbents] == ["1OEM1", "2RES2"] and lc.reseller_wins_nsn == 1
    assert lc.p_winnable == pytest.approx((1 + SHRINK_K * 0.5) / (5 + SHRINK_K))
    assert "resellers won 1 of 5" in lc.reason
    assert [c.cage for c in lc.fsc_players] == ["3RES3"]  # incumbents are not repeated as supply-class players
    d2 = comp.for_line("5305000000002", as_of=TODAY)
    assert d2.reseller_share_nsn == 1.0 and d2.p_winnable == pytest.approx((3 + SHRINK_K * 0.5) / (3 + SHRINK_K))


def test_manufacturer_dominated_part_is_hard_and_own_wins_count_as_reseller():
    rows = [("5305000000009", TODAY - timedelta(days=30 * i), "1OEM1", 10.0, 10) for i in range(1, 7)]
    comp = FscCompetitors.build("5305", awards(rows), {"1OEM1"}, as_of=TODAY)
    lc = comp.for_line("5305000000009", as_of=TODAY)
    assert "manufacturer" in lc.reason and lc.p_winnable == P_WINNABLE_FLOOR
    # If the OEM CAGE is the broker's own organization, its wins are a reseller's wins.
    mine = comp.for_line("5305000000009", org_cage="1OEM1", as_of=TODAY)
    # (shrunk toward this tiny supply class's reseller share of 0, hence not 1.0)
    assert mine.reseller_wins_nsn == 6 and mine.p_winnable == pytest.approx(6 / (6 + SHRINK_K))


def test_competitor_view_of_p_win_falls_with_the_bid():
    rows = [("5305000000001", TODAY - timedelta(days=30 * i), "2RES2", 10.0 * 0.97 ** i, 100) for i in range(6, 0, -1)]
    comp = FscCompetitors.build("5305", awards(rows), set(), as_of=TODAY)
    lc = comp.for_line("5305000000001", as_of=TODAY)
    ref = 10.0
    low, mid, high = lc.p_win(7.0, ref), lc.p_win(9.7, ref), lc.p_win(12.0, ref)
    assert 1.0 >= low > mid > high >= 0.0
    assert lc.p_win(9.7, None) is None
    # The incumbent won every award: it bids with probability 0.95, so even a very low bid is not certain.
    assert low < 1.0 and low > 0.9


def test_evaluate_contestability_scores_against_the_constant_share():
    rows = []
    d = TODAY - timedelta(days=700)
    for i in range(12):
        rows.append(("5305000000001", d + timedelta(days=30 * i), "1OEM1", 10.0, 10))  # OEM part
        rows.append(("5305000000002", d + timedelta(days=30 * i), "2RES2" if i % 2 else "3RES3", 5.0, 10))  # reseller part
    res = evaluate_contestability(awards(rows), {"1OEM1"}, "5305")
    assert res["n"] == 22 and res["brier"] < res["baseline_brier"]  # knowing the part's history beats the FSC share
    assert res["bins"] and all(0 <= b["observed"] <= 1 for b in res["bins"])


def test_automated_price_band_from_typed_awards():
    import numpy as np

    rows = []
    d = TODAY - timedelta(days=900)
    price = 10.0
    for i in range(40):  # one part, a long automated history: prices drift up 2% an award, one 60% jump
        ratio = 1.6 if i == 20 else 1.02
        price *= ratio
        rows.append(("5305000000001", d + timedelta(days=20 * i), "2RES2", price, 10, f"SPE4A6{20 + i // 10}T{i:04d}"))
    df = pd.DataFrame(rows, columns=["nsn", "award_date", "awardee_cage", "unit_price", "quantity", "solicitation_number"])
    comp = FscCompetitors.build("5305", df, set(), as_of=TODAY)
    assert comp.n_automated_ratios == 39 and comp.automated_ratio_p95 is not None
    assert 1.02 <= comp.automated_ratio_p95 < 1.6  # the outlier is above the 95th percentile of 39 awards
    assert comp.automated_ratio_p95 == pytest.approx(float(np.quantile(comp.awards["ratio"].dropna(), 0.95)))
    # Too few awards: no band.
    small = FscCompetitors.build("5305", df.head(10), set(), as_of=TODAY)
    assert small.automated_ratio_p95 is None and small.n_automated_ratios == 0
