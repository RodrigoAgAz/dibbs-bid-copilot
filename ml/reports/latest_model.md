# Latest pricing model

Model run **#5**: **promoted** to active (pinball 0.0571 < active 0.0576).

_Trained on 5,590 award rows, of which 5,590 are the synthetic demo history from `make seed`. Metrics on synthetic data show the pipeline works; they are not evidence about real DIBBS prices._

## Data and time windows

Time-based split, never random: train on everything up to T-6 months, conformal calibration on T-6..T-3 months, test on T-3 months..T.

| Window | Dates | Rows |
|---|---|---|
| Train | 2023-01-01 .. 2026-04-09 | 4,886 |
| Calibrate | 2026-04-09 .. 2026-07-09 | 344 |
| Test | 2026-07-09 .. 2026-10-09 | 360 |


Cold-start share of test rows (no prior award for the NSN): 0.0%.

## Accuracy on the test window

Target: log(winning unit price / reference price). Pinball loss is averaged over the 11 quantiles in log-price space (**primary metric**, lower is better).

| Metric | LightGBM + conformal | LightGBM (uncalibrated) | Baseline |
|---|---|---|---|
| Mean pinball loss | **0.0571** | 0.0575 | 0.0802 |
| MAPE of median | 17.3% | 17.7% | 23.6% |
| Median abs. % error | 14.2% | 14.2% | 17.6% |
| 80% interval coverage | 79.2% | 76.1% | 83.3% |
| 90% interval coverage | 89.7% | 85.3% | 91.1% |

Previously active model on the same test window: pinball 0.0576, 80% coverage 78.1%.

![Pinball loss by quantile](pinball.png)

### Quantile coverage

| Level | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Model | 0.05 | 0.10 | 0.19 | 0.30 | 0.40 | 0.51 | 0.58 | 0.67 | 0.78 | 0.89 | 0.95 |
| Before conformal | 0.08 | 0.13 | 0.25 | 0.35 | 0.46 | 0.53 | 0.60 | 0.68 | 0.78 | 0.89 | 0.94 |
| Baseline | 0.04 | 0.09 | 0.19 | 0.28 | 0.38 | 0.47 | 0.61 | 0.72 | 0.83 | 0.92 | 0.96 |

![Coverage](coverage.png)

## P(win) calibration

For every test line, 5 bids are sampled around the reference price (b = ref x exp(N(0, 0.25))). Predicted P(win) = P(W > b) is bucketed and compared with how often the actual winning price was above the bid.

| Predicted bucket | Mean predicted | Observed | Count |
|---|---|---|---|
| 0.0-0.1 | 0.036 | 0.029 | 377 |
| 0.1-0.2 | 0.148 | 0.132 | 174 |
| 0.2-0.3 | 0.249 | 0.281 | 121 |
| 0.3-0.4 | 0.348 | 0.443 | 106 |
| 0.4-0.5 | 0.449 | 0.435 | 108 |
| 0.5-0.6 | 0.552 | 0.491 | 106 |
| 0.6-0.7 | 0.648 | 0.631 | 111 |
| 0.7-0.8 | 0.756 | 0.846 | 169 |
| 0.8-0.9 | 0.851 | 0.863 | 153 |
| 0.9-1.0 | 0.961 | 0.979 | 375 |

Brier score 0.1162, expected calibration error 0.0294 (baseline: Brier 0.1699, ECE 0.0424).

![P(win) calibration](calibration.png)

## Bidding backtest

Assumption: landed cost = winning price x U(0.6, 0.95); bid wins iff bid < winning price. Model policy uses the recommendation engine with a 5% minimum margin and skips no-bid lines.

| Policy | Bids | Wins | Win rate | Total profit | Profit per bid | Wins below cost |
|---|---|---|---|---|---|---|
| model | 356 | 116 | 32.6% | $44,637 | $125.38 | 0 |
| last_minus_2pct | 360 | 207 | 57.5% | $16,163 | $44.90 | 78 |
| cost_plus_15pct | 360 | 275 | 76.4% | $82,077 | $227.99 | 0 |

![Backtest](backtest.png)

## Top features (median model, share of gain)

| Feature | Importance |
|---|---|
| log_last_to_dod | 14.6% |
| qty_rel_median | 11.0% |
| log_qty_vs_last | 10.9% |
| f_min | 10.7% |
| f_max | 9.1% |
| f_last3_median | 6.4% |
| trend_per_year | 4.9% |
| log_ref_price | 4.2% |
| volatility | 4.1% |
| log_quantity | 3.5% |
| days_since_last_award | 3.2% |
| first_article | 2.8% |
| inflation_change | 2.5% |
| fsc | 2.2% |
| type_code | 2.2% |

## Caveats

- Only winning prices are observed, never losing bids, so P(win) = P(W > bid) is an approximation: it assumes the lowest acceptable price wins and ignores past-performance scoring and ties.
- The backtest's landed-cost assumption is synthetic; real profit depends on actual supplier quotes.
- Under that assumption the landed cost is itself a noisy copy of the winning price (cost ~ 0.775 x W), so `cost_plus_15pct` effectively sees the answer: its bid is W x U(0.69, 1.09). The model policy deliberately does not use cost to predict W (only to compute margin), so this comparison flatters cost-plus. Real supplier costs are far less tightly tied to the winning price; `last_minus_2pct` is the fairer naive benchmark.
