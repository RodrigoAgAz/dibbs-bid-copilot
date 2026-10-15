"""Write ml/reports/latest_model.md (+ PNG charts) from a training run's metrics."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def _fmt(v, pct=False, money=False) -> str:
    if v is None:
        return "n/a"
    if pct:
        return f"{100 * v:.1f}%"
    if money:
        return f"${v:,.0f}"
    return f"{v:.4f}"


def _charts(metrics: dict, out_dir: Path) -> dict[str, str]:
    files = {}
    # 1. P(win) calibration
    fig, ax = plt.subplots(figsize=(4.8, 4.2), dpi=120)
    for key, label, color in (("pwin_calibration", "LightGBM + conformal", "#1d4ed8"),
                              ("baseline_pwin_calibration", "Baseline", "#9ca3af")):
        bins = metrics.get(key, {}).get("bins", [])
        if bins:
            ax.plot([b["predicted"] for b in bins], [b["observed"] for b in bins], "o-", label=label, color=color)
    ax.plot([0, 1], [0, 1], "--", color="#6b7280", lw=1, label="perfect calibration")
    ax.set_xlabel("Predicted P(win) = P(W > bid)")
    ax.set_ylabel("Observed win frequency")
    ax.set_title("P(win) calibration (test window)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    files["calibration"] = "calibration.png"
    fig.savefig(out_dir / files["calibration"])
    plt.close(fig)

    # 2. Quantile coverage
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=120)
    qc = metrics["model"]["quantile_coverage"]
    lv = [float(k) for k in qc]
    ax.plot(lv, lv, "--", color="#6b7280", lw=1, label="nominal")
    ax.plot(lv, list(qc.values()), "o-", color="#1d4ed8", label="model (calibrated)")
    ax.plot(lv, list(metrics["uncalibrated"]["quantile_coverage"].values()), "s-", color="#f59e0b", alpha=0.8,
            label="before conformal")
    ax.plot(lv, list(metrics["baseline"]["quantile_coverage"].values()), "^-", color="#9ca3af", label="baseline")
    ax.set_xlabel("Quantile level")
    ax.set_ylabel("Empirical P(W <= q)")
    ax.set_title("Quantile coverage (test window)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    files["coverage"] = "coverage.png"
    fig.savefig(out_dir / files["coverage"])
    plt.close(fig)

    # 3. Pinball per quantile
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=120)
    for key, label, color in (("model", "model", "#1d4ed8"), ("baseline", "baseline", "#9ca3af")):
        pq = metrics[key]["pinball_per_quantile"]
        ax.plot([float(k) for k in pq], list(pq.values()), "o-", label=label, color=color)
    ax.set_xlabel("Quantile level")
    ax.set_ylabel("Pinball loss (log price)")
    ax.set_title("Pinball loss by quantile")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    files["pinball"] = "pinball.png"
    fig.savefig(out_dir / files["pinball"])
    plt.close(fig)

    # 4. Backtest
    pol = metrics["backtest"]["policies"]
    names = list(pol)
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), dpi=120)
    colors = ["#1d4ed8", "#9ca3af", "#f59e0b"]
    axes[0].bar(names, [pol[n]["total_profit"] for n in names], color=colors)
    axes[0].set_title("Simulated total profit")
    axes[1].bar(names, [100 * pol[n]["win_rate"] for n in names], color=colors)
    axes[1].set_title("Win rate (%)")
    for a in axes:
        a.tick_params(axis="x", labelsize=8)
        a.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    files["backtest"] = "backtest.png"
    fig.savefig(out_dir / files["backtest"])
    plt.close(fig)
    return files


def write_model_report(metrics: dict, run_id, promoted: bool, promote_reason: str, out_dir: Path | None = None,
                       data_note: str = "") -> Path:
    out_dir = out_dir or REPORTS
    out_dir.mkdir(parents=True, exist_ok=True)
    files = _charts(metrics, out_dir)
    m, b, raw = metrics["model"], metrics["baseline"], metrics["uncalibrated"]
    w = metrics["windows"]
    lines = [
        "# Latest pricing model",
        "",
        f"Model run **#{run_id}**: {'**promoted** to active' if promoted else 'not promoted'} ({promote_reason}).",
        "",
        data_note,
        "",
        "## Data and time windows",
        "",
        "Time-based split, never random: train on everything up to T-4 months, conformal calibration on "
        "T-4..T-3 months, test on T-3 months..T. The deployed model is then refit on everything up to T-1 month "
        "and calibrated on the last month.",
        "",
        "| Window | Dates | Rows |",
        "|---|---|---|",
        f"| Train | {w['train'][0]} .. {w['train'][1]} | {metrics['rows']['train']:,} |",
        f"| Calibrate | {w['calib'][0]} .. {w['calib'][1]} | {metrics['rows']['calib']:,} |",
        f"| Test | {w['test'][0]} .. {w['test'][1]} | {metrics['rows']['test']:,} |",
        "",
        (f"_Note: {w['fallback']}._" if "fallback" in w else ""),
        f"Cold-start share of test rows (no prior award for the NSN): {_fmt(metrics['cold_share_test'], pct=True)}.",
        "",
        "## Accuracy on the test window",
        "",
        "Target: log(winning unit price / reference price). Pinball loss is averaged over the 11 quantiles "
        "in log-price space (**primary metric**, lower is better).",
        "",
        "| Metric | LightGBM + conformal | LightGBM (uncalibrated) | Baseline |",
        "|---|---|---|---|",
        f"| Mean pinball loss | **{_fmt(m['pinball_mean'])}** | {_fmt(raw['pinball_mean'])} | {_fmt(b['pinball_mean'])} |",
        f"| MAPE of median | {_fmt(m['mape_median'], pct=True)} | {_fmt(raw['mape_median'], pct=True)} | {_fmt(b['mape_median'], pct=True)} |",
        f"| Median abs. % error | {_fmt(m['median_abs_pct_error'], pct=True)} | {_fmt(raw['median_abs_pct_error'], pct=True)} | {_fmt(b['median_abs_pct_error'], pct=True)} |",
        f"| 80% interval coverage | {_fmt(m['coverage_80'], pct=True)} | {_fmt(raw['coverage_80'], pct=True)} | {_fmt(b['coverage_80'], pct=True)} |",
        f"| 90% interval coverage | {_fmt(m['coverage_90'], pct=True)} | {_fmt(raw['coverage_90'], pct=True)} | {_fmt(b['coverage_90'], pct=True)} |",
        "",
    ]
    if "warm" in m:
        lines += [
            "| Segment | Rows | Pinball | MAPE median | 80% cov. | 90% cov. |",
            "|---|---|---|---|---|---|",
        ]
        for seg in ("warm", "cold"):
            s = m[seg]
            lines.append(
                f"| {'NSN has history' if seg == 'warm' else 'Cold start'} | {s['n']:,} | {_fmt(s['pinball_mean'])} | "
                f"{_fmt(s['mape_median'], pct=True)} | {_fmt(s['coverage_80'], pct=True)} | {_fmt(s['coverage_90'], pct=True)} |"
            )
        lines.append("")
    act = metrics.get("active_model_on_same_test")
    if act and "pinball_mean" in act:
        lines += [f"Previously active model on the same test window: pinball {_fmt(act['pinball_mean'])}, "
                  f"80% coverage {_fmt(act['coverage_80'], pct=True)}.", ""]
    lines += [
        f"![Pinball loss by quantile]({files['pinball']})",
        "",
        "### Quantile coverage",
        "",
        "| Level | " + " | ".join(m["quantile_coverage"].keys()) + " |",
        "|---|" + "---|" * len(m["quantile_coverage"]),
        "| Model | " + " | ".join(f"{v:.2f}" for v in m["quantile_coverage"].values()) + " |",
        "| Before conformal | " + " | ".join(f"{v:.2f}" for v in raw["quantile_coverage"].values()) + " |",
        "| Baseline | " + " | ".join(f"{v:.2f}" for v in b["quantile_coverage"].values()) + " |",
        "",
        f"![Coverage]({files['coverage']})",
        "",
        "## P(win) calibration",
        "",
        "For every test line, 5 bids are sampled around the reference price (b = ref x exp(N(0, 0.25))). "
        "Predicted P(win) = P(W > b) is bucketed and compared with how often the actual winning price "
        "was above the bid.",
        "",
        "| Predicted bucket | Mean predicted | Observed | Count |",
        "|---|---|---|---|",
    ]
    for row in metrics["pwin_calibration"]["bins"]:
        lines.append(f"| {row['bin']} | {row['predicted']:.3f} | {row['observed']:.3f} | {row['count']:,} |")
    pc = metrics["pwin_calibration"]
    lines += [
        "",
        f"Brier score {pc['brier']:.4f}, expected calibration error {pc['ece']:.4f} "
        f"(baseline: Brier {metrics['baseline_pwin_calibration']['brier']:.4f}, "
        f"ECE {metrics['baseline_pwin_calibration']['ece']:.4f}).",
        "",
        f"![P(win) calibration]({files['calibration']})",
        "",
        "## Bidding backtest",
        "",
        f"Assumption: {metrics['backtest']['assumption']}. Every policy is held to the same rules: no bid below "
        f"landed cost + {100 * metrics['backtest']['min_margin_pct']:.0f}% and none with less than "
        f"{100 * metrics['backtest'].get('min_p_win', 0.05):.0f}% chance of winning; each bid placed is charged "
        f"${metrics['backtest'].get('pursuit_cost', 0):,.0f} of effort.",
        "",
        "| Policy | Bids | Withheld by rules | Wins | Win rate | Total profit | Net of effort | Net per line | Wins below cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in metrics["backtest"]["policies"].items():
        lines.append(
            f"| {name} | {r['bids']:,} | {r.get('withheld_by_rules', 0):,} | {r['wins']:,} | {_fmt(r['win_rate'], pct=True)} | "
            f"{_fmt(r['total_profit'], money=True)} | {_fmt(r.get('net_profit', r['total_profit']), money=True)} | "
            f"${r.get('net_profit_per_line', 0):,.2f} | {r['losing_wins']:,} |"
        )
    lines += [
        "",
        f"![Backtest]({files['backtest']})",
        "",
        "## Top features (median model, share of gain)",
        "",
        "| Feature | Importance |",
        "|---|---|",
    ]
    for f, v in metrics["feature_importance"].items():
        lines.append(f"| {f} | {100 * v:.1f}% |")
    lines += [
        "",
        "## Caveats",
        "",
        "- Only winning prices are observed, never losing bids, so P(win) = P(W > bid) is an approximation: "
        "it assumes the lowest acceptable price wins and ignores past-performance scoring and ties.",
        "- The backtest's landed-cost assumption is synthetic; real profit depends on actual supplier quotes.",
        "- Under that assumption the landed cost is itself a noisy copy of the winning price "
        "(cost ~ 0.775 x W), so `cost_plus_15pct` effectively sees the answer: its bid is W x U(0.69, 1.09). "
        "The model policy deliberately does not use cost to predict W (only to compute margin), so this "
        "comparison flatters cost-plus. Real supplier costs are far less tightly tied to the winning price; "
        "`last_minus_2pct` is the fairer naive benchmark.",
    ]
    path = out_dir / "latest_model.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
