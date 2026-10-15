# Latest pricing model

Model run **#2**: not promoted (pinball 0.0689 >= active 0.0689).

_Trained on 1,458 award rows, of which 1,454 are the synthetic demo history from `make seed`. Metrics on synthetic data show the pipeline works; they are not evidence about real DIBBS prices._

## Data and time windows

Time-based split, never random: train on everything up to T-4 months, conformal calibration on T-4..T-3 months, test on T-3 months..T. The deployed model is then refit on everything up to T-1 month and calibrated on the last month.

| Window | Dates | Rows |
|---|---|---|
| Train | 2023-01-01 .. 2026-06-15 | 1,354 |
| Calibrate | 2026-06-15 .. 2026-07-15 | 33 |
| Test | 2026-07-15 .. 2026-10-15 | 71 |


Cold-start share of test rows (no prior award for the NSN): 0.0%.

## Accuracy on the test window

Target: log(winning unit price / reference price). Pinball loss is averaged over the 11 quantiles in log-price space (**primary metric**, lower is better).

| Metric | LightGBM + conformal | LightGBM (uncalibrated) | Baseline |
|---|---|---|---|
| Mean pinball loss | **0.0689** | 0.0636 | 0.0931 |
| MAPE of median | 19.2% | 18.7% | 29.3% |
| Median abs. % error | 14.2% | 13.5% | 19.2% |
| 80% interval coverage | 85.9% | 66.2% | 77.5% |
| 90% interval coverage | 90.1% | 77.5% | 85.9% |

Previously active model on the same test window: pinball 0.0689, 80% coverage 85.9%.

![Pinball loss by quantile](pinball.png)

### Quantile coverage

| Level | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Model | 0.10 | 0.13 | 0.14 | 0.28 | 0.38 | 0.55 | 0.73 | 0.90 | 0.97 | 0.99 | 1.00 |
| Before conformal | 0.17 | 0.20 | 0.28 | 0.32 | 0.38 | 0.48 | 0.56 | 0.68 | 0.76 | 0.86 | 0.94 |
| Baseline | 0.10 | 0.14 | 0.23 | 0.38 | 0.45 | 0.54 | 0.62 | 0.70 | 0.83 | 0.92 | 0.96 |

![Coverage](coverage.png)

## P(win) calibration

For every test line, 5 bids are sampled around the reference price (b = ref x exp(N(0, 0.25))). Predicted P(win) = P(W > b) is bucketed and compared with how often the actual winning price was above the bid.

| Predicted bucket | Mean predicted | Observed | Count |
|---|---|---|---|
| 0.0-0.1 | 0.065 | 0.000 | 22 |
| 0.1-0.2 | 0.150 | 0.000 | 23 |
| 0.2-0.3 | 0.256 | 0.050 | 60 |
| 0.3-0.4 | 0.350 | 0.205 | 44 |
| 0.4-0.5 | 0.439 | 0.212 | 33 |
| 0.5-0.6 | 0.552 | 0.739 | 23 |
| 0.6-0.7 | 0.661 | 0.756 | 41 |
| 0.7-0.8 | 0.742 | 0.780 | 41 |
| 0.8-0.9 | 0.841 | 0.810 | 21 |
| 0.9-1.0 | 0.979 | 0.936 | 47 |

Brier score 0.1334, expected calibration error 0.1228 (baseline: Brier 0.1923, ECE 0.0601).

![P(win) calibration](calibration.png)

## Bidding backtest

Assumption: landed cost = winning price x U(0.6, 0.95); bid wins iff bid < winning price. Every policy is held to the same rules: no bid below landed cost + 5% and none with less than 5% chance of winning; each bid placed is charged $15 of effort.

| Policy | Bids | Withheld by rules | Wins | Win rate | Total profit | Net of effort | Net per line | Wins below cost |
|---|---|---|---|---|---|---|---|---|
| model | 71 | 0 | 4 | 5.6% | $2,488 | $1,423 | $20.04 | 0 |
| last_minus_2pct | 52 | 19 | 17 | 32.7% | $2,994 | $2,214 | $31.19 | 0 |
| cost_plus_15pct | 71 | 0 | 56 | 78.9% | $5,298 | $4,233 | $59.62 | 0 |

![Backtest](backtest.png)

## Top features (median model, share of gain)

| Feature | Importance |
|---|---|
| qty_rel_median | 10.3% |
| f_min | 9.0% |
| log_qty_vs_last | 8.1% |
| log_last_to_dod | 7.8% |
| volatility | 7.4% |
| f_last3_median | 7.0% |
| trend_per_year | 6.9% |
| f_max | 6.5% |
| log_quantity | 6.3% |
| log_ref_price | 5.8% |
| days_since_last_award | 5.1% |
| inflation_change | 4.0% |
| incumbent_share | 3.3% |
| n_prior_awards | 2.6% |
| type_code | 2.2% |

## Caveats

- Only winning prices are observed, never losing bids, so P(win) = P(W > bid) is an approximation: it assumes the lowest acceptable price wins and ignores past-performance scoring and ties.
- The backtest's landed-cost assumption is synthetic; real profit depends on actual supplier quotes.
- Under that assumption the landed cost is itself a noisy copy of the winning price (cost ~ 0.775 x W), so `cost_plus_15pct` effectively sees the answer: its bid is W x U(0.69, 1.09). The model policy deliberately does not use cost to predict W (only to compute margin), so this comparison flatters cost-plus. Real supplier costs are far less tightly tied to the winning price; `last_minus_2pct` is the fairer naive benchmark.
