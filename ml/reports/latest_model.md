# Latest pricing model

Model run **#1**: **promoted** to active (forced (no comparable active model)).

_Trained on 5,316 award rows, of which 5,316 are the synthetic demo history from `make seed`. Metrics on synthetic data show the pipeline works; they are not evidence about real DIBBS prices._

## Data and time windows

Time-based split, never random: train on everything up to T-6 months, conformal calibration on T-6..T-3 months, test on T-3 months..T.

| Window | Dates | Rows |
|---|---|---|
| Train | 2023-01-01 .. 2026-04-10 | 4,617 |
| Calibrate | 2026-04-10 .. 2026-07-10 | 381 |
| Test | 2026-07-10 .. 2026-10-10 | 318 |


Cold-start share of test rows (no prior award for the NSN): 0.0%.

## Accuracy on the test window

Target: log(winning unit price / reference price). Pinball loss is averaged over the 11 quantiles in log-price space (**primary metric**, lower is better).

| Metric | LightGBM + conformal | LightGBM (uncalibrated) | Baseline |
|---|---|---|---|
| Mean pinball loss | **0.0591** | 0.0598 | 0.0878 |
| MAPE of median | 17.2% | 16.9% | 26.7% |
| Median abs. % error | 14.4% | 14.9% | 20.7% |
| 80% interval coverage | 78.3% | 69.2% | 78.0% |
| 90% interval coverage | 89.0% | 83.3% | 87.7% |

![Pinball loss by quantile](pinball.png)

### Quantile coverage

| Level | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Model | 0.05 | 0.12 | 0.22 | 0.31 | 0.39 | 0.49 | 0.61 | 0.70 | 0.81 | 0.91 | 0.94 |
| Before conformal | 0.07 | 0.16 | 0.24 | 0.31 | 0.39 | 0.47 | 0.57 | 0.64 | 0.73 | 0.85 | 0.91 |
| Baseline | 0.06 | 0.11 | 0.22 | 0.32 | 0.45 | 0.54 | 0.63 | 0.72 | 0.81 | 0.89 | 0.94 |

![Coverage](coverage.png)

## P(win) calibration

For every test line, 5 bids are sampled around the reference price (b = ref x exp(N(0, 0.25))). Predicted P(win) = P(W > b) is bucketed and compared with how often the actual winning price was above the bid.

| Predicted bucket | Mean predicted | Observed | Count |
|---|---|---|---|
| 0.0-0.1 | 0.045 | 0.034 | 323 |
| 0.1-0.2 | 0.146 | 0.085 | 164 |
| 0.2-0.3 | 0.252 | 0.190 | 100 |
| 0.3-0.4 | 0.354 | 0.325 | 120 |
| 0.4-0.5 | 0.446 | 0.479 | 119 |
| 0.5-0.6 | 0.549 | 0.596 | 99 |
| 0.6-0.7 | 0.648 | 0.674 | 95 |
| 0.7-0.8 | 0.752 | 0.719 | 135 |
| 0.8-0.9 | 0.858 | 0.865 | 141 |
| 0.9-1.0 | 0.963 | 0.966 | 294 |

Brier score 0.1213, expected calibration error 0.0255 (baseline: Brier 0.1827, ECE 0.0315).

![P(win) calibration](calibration.png)

## Bidding backtest

Assumption: landed cost = winning price x U(0.6, 0.95); bid wins iff bid < winning price. Model policy uses the recommendation engine with a 5% minimum margin and skips no-bid lines.

| Policy | Bids | Wins | Win rate | Total profit | Profit per bid | Wins below cost |
|---|---|---|---|---|---|---|
| model | 310 | 115 | 37.1% | $96,023 | $309.75 | 0 |
| last_minus_2pct | 318 | 162 | 50.9% | $29,551 | $92.93 | 72 |
| cost_plus_15pct | 318 | 245 | 77.0% | $85,657 | $269.36 | 0 |

![Backtest](backtest.png)

## Top features (median model, share of gain)

| Feature | Importance |
|---|---|
| log_last_to_dod | 17.5% |
| log_qty_vs_last | 11.5% |
| qty_rel_median | 11.0% |
| f_max | 8.0% |
| f_last3_median | 7.9% |
| f_min | 6.9% |
| trend_per_year | 5.0% |
| volatility | 4.6% |
| log_quantity | 3.9% |
| log_ref_price | 3.6% |
| inflation_change | 3.6% |
| days_since_last_award | 2.9% |
| incumbent_share | 2.4% |
| fsc | 2.2% |
| first_article | 2.0% |

## Caveats

- Only winning prices are observed, never losing bids, so P(win) = P(W > bid) is an approximation: it assumes the lowest acceptable price wins and ignores past-performance scoring and ties.
- The backtest's landed-cost assumption is synthetic; real profit depends on actual supplier quotes.
- Under that assumption the landed cost is itself a noisy copy of the winning price (cost ~ 0.775 x W), so `cost_plus_15pct` effectively sees the answer: its bid is W x U(0.69, 1.09). The model policy deliberately does not use cost to predict W (only to compute margin), so this comparison flatters cost-plus. Real supplier costs are far less tightly tied to the winning price; `last_minus_2pct` is the fairer naive benchmark.
