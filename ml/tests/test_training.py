"""End-to-end training on synthetic data: beats the baseline, calibrated coverage, promotion logic."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from dibbs_ml.models import PricingModel, apply_conformal, fit_conformal
from dibbs_ml.report import write_model_report
from dibbs_ml.synthetic import generate
from dibbs_ml.train import save_artifact, should_promote, train_from_awards

AS_OF = date(2026, 6, 30)


@pytest.fixture(scope="module")
def result():
    data = generate(n_nsns=180, start=date(2023, 1, 1), end=AS_OF, n_open_lines=10, seed=11)
    return train_from_awards(data.awards, as_of=AS_OF)


def test_model_beats_baseline_on_primary_metric(result):
    m = result.metrics
    assert m["pinball_mean"] < m["baseline"]["pinball_mean"]
    assert m["model"]["mape_median"] < m["baseline"]["mape_median"]


def test_conformal_coverage_is_close_to_nominal(result):
    m = result.metrics["model"]
    assert 0.70 <= m["coverage_80"] <= 0.90
    assert 0.82 <= m["coverage_90"] <= 0.97


def test_time_windows_do_not_overlap(result):
    w = result.metrics["windows"]
    assert w["train"][1] <= w["calib"][0] <= w["calib"][1] <= w["test"][0]


def test_backtest_and_report(result, tmp_dir):
    bt = result.metrics["backtest"]["policies"]
    assert set(bt) == {"model", "last_minus_2pct", "cost_plus_15pct"}
    assert bt["model"]["losing_wins"] == 0  # the engine never bids below cost
    # Every policy is held to the same margin and win-probability rules, and charged for effort.
    assert all(r["losing_wins"] == 0 for r in bt.values())
    assert all({"net_profit", "net_profit_per_line", "withheld_by_rules"} <= set(r) for r in bt.values())
    assert bt["last_minus_2pct"]["withheld_by_rules"] >= 0 and bt["model"]["withheld_by_rules"] == 0
    assert bt["cost_plus_15pct"]["net_profit"] == pytest.approx(
        bt["cost_plus_15pct"]["total_profit"] - result.metrics["backtest"]["pursuit_cost"] * bt["cost_plus_15pct"]["bids"])
    path = write_model_report(result.metrics, run_id=1, promoted=True, promote_reason="test", out_dir=tmp_dir)
    text = path.read_text(encoding="utf-8")
    for needle in ("pinball", "80% interval coverage", "P(win) calibration", "cost_plus_15pct", "last_minus_2pct"):
        assert needle in text
    assert (tmp_dir / "calibration.png").exists()


def test_artifact_roundtrip_and_promotion(result, tmp_dir):
    path = save_artifact(result.model, tmp_dir, "t1")
    loaded = PricingModel.load(path)
    test = result.frame.tail(20)
    a, _ = result.model.predict_log(test)
    b, _ = loaded.predict_log(test)
    np.testing.assert_allclose(a, b)
    ok, _ = should_promote({"pinball_mean": 0.05})
    assert ok
    ok, _ = should_promote({"pinball_mean": 0.05, "active_model_on_same_test": {"pinball_mean": 0.04}})
    assert not ok


def test_explanations_have_contributions(result):
    expl = result.model.explain(result.frame.tail(1))
    assert len(expl) == 6
    assert all({"feature", "contribution", "effect_pct"} <= set(e) for e in expl)


def test_fit_conformal_recovers_nominal_coverage():
    rng = np.random.default_rng(5)
    levels = (0.1, 0.5, 0.9)
    y = rng.normal(0, 1, 4000)
    biased = np.tile(np.array([-0.5, 0.3, 0.6]), (4000, 1))  # badly calibrated quantiles
    d = fit_conformal(biased[:2000], y[:2000], levels)
    adj = apply_conformal(biased[2000:], d)
    cov = (y[2000:, None] <= adj).mean(axis=0)
    np.testing.assert_allclose(cov, levels, atol=0.03)
