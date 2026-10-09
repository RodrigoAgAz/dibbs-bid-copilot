# Latest pricing model

Model run **#1**: **promoted** to active (forced (no comparable active model)).

_Trained on 5,960 real award rows from the database._

## Data and time windows

Time-based split, never random: train on everything up to T-6 months, conformal calibration on T-6..T-3 months, test on T-3 months..T.

| Window | Dates | Rows |
|---|---|---|
| Train | 1994-09-12 .. 2026-04-09 | 5,507 |
| Calibrate | 2026-04-09 .. 2026-07-09 | 207 |
| Test | 2026-07-09 .. 2026-10-09 | 235 |


Cold-start share of test rows (no prior award for the NSN): 30.6%.

## Accuracy on the test window

Target: log(winning unit price / reference price). Pinball loss is averaged over the 11 quantiles in log-price space (**primary metric**, lower is better).

| Metric | LightGBM + conformal | LightGBM (uncalibrated) | Baseline |
|---|---|---|---|
| Mean pinball loss | **0.2677** | 0.2730 | 0.2958 |
| MAPE of median | 62.8% | 61.3% | 85.6% |
| Median abs. % error | 14.9% | 15.6% | 17.3% |
| 80% interval coverage | 77.9% | 72.3% | 77.0% |
| 90% interval coverage | 86.8% | 83.0% | 84.3% |

| Segment | Rows | Pinball | MAPE median | 80% cov. | 90% cov. |
|---|---|---|---|---|---|
| NSN has history | 163 | 0.1218 | 21.3% | 78.5% | 87.7% |
| Cold start | 72 | 0.5980 | 156.6% | 76.4% | 84.7% |

![Pinball loss by quantile](pinball.png)

### Quantile coverage

| Level | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Model | 0.04 | 0.09 | 0.18 | 0.26 | 0.32 | 0.44 | 0.56 | 0.66 | 0.77 | 0.86 | 0.91 |
| Before conformal | 0.05 | 0.11 | 0.20 | 0.31 | 0.40 | 0.51 | 0.59 | 0.69 | 0.77 | 0.83 | 0.88 |
| Baseline | 0.06 | 0.07 | 0.17 | 0.35 | 0.46 | 0.63 | 0.66 | 0.71 | 0.77 | 0.84 | 0.90 |

![Coverage](coverage.png)

## P(win) calibration

For every test line, 5 bids are sampled around the reference price (b = ref x exp(N(0, 0.25))). Predicted P(win) = P(W > b) is bucketed and compared with how often the actual winning price was above the bid.

| Predicted bucket | Mean predicted | Observed | Count |
|---|---|---|---|
| 0.0-0.1 | 0.065 | 0.048 | 146 |
| 0.1-0.2 | 0.143 | 0.199 | 161 |
| 0.2-0.3 | 0.248 | 0.382 | 68 |
| 0.3-0.4 | 0.354 | 0.421 | 107 |
| 0.4-0.5 | 0.448 | 0.569 | 102 |
| 0.5-0.6 | 0.552 | 0.576 | 85 |
| 0.6-0.7 | 0.650 | 0.530 | 83 |
| 0.7-0.8 | 0.748 | 0.735 | 83 |
| 0.8-0.9 | 0.858 | 0.893 | 103 |
| 0.9-1.0 | 0.965 | 0.954 | 237 |

Brier score 0.1537, expected calibration error 0.0506 (baseline: Brier 0.1740, ECE 0.0345).

![P(win) calibration](calibration.png)

## Bidding backtest

Assumption: landed cost = winning price x U(0.6, 0.95); bid wins iff bid < winning price. Model policy uses the recommendation engine with a 5% minimum margin and skips no-bid lines.

| Policy | Bids | Wins | Win rate | Total profit | Profit per bid | Wins below cost |
|---|---|---|---|---|---|---|
| model | 215 | 91 | 42.3% | $464,061 | $2,158.42 | 0 |
| last_minus_2pct | 163 | 136 | 83.4% | $-615,488 | $-3,776.00 | 26 |
| cost_plus_15pct | 235 | 182 | 77.4% | $870,157 | $3,702.80 | 0 |

![Backtest](backtest.png)

## Top features (median model, share of gain)

| Feature | Importance |
|---|---|
| inflation_change | 17.8% |
| days_since_last_award | 13.8% |
| log_qty_vs_last | 13.5% |
| qty_rel_median | 9.6% |
| volatility | 8.5% |
| trend_per_year | 5.1% |
| f_last3_median | 4.4% |
| log_ref_price | 4.4% |
| log_quantity | 4.0% |
| incumbent_share | 3.9% |
| f_min | 3.1% |
| f_max | 3.0% |
| n_prior_awards | 2.8% |
| fsc | 2.7% |
| n_approved_sources | 2.5% |

## Caveats

- Only winning prices are observed, never losing bids, so P(win) = P(W > bid) is an approximation: it assumes the lowest acceptable price wins and ignores past-performance scoring and ties.
- The backtest's landed-cost assumption is synthetic; real profit depends on actual supplier quotes.
- Under that assumption the landed cost is itself a noisy copy of the winning price (cost ~ 0.775 x W), so `cost_plus_15pct` effectively sees the answer: its bid is W x U(0.69, 1.09). The model policy deliberately does not use cost to predict W (only to compute margin), so this comparison flatters cost-plus. Real supplier costs are far less tightly tied to the winning price; `last_minus_2pct` is the fairer naive benchmark.
