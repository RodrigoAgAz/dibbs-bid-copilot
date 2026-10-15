# Pricing research (2026-10-15)

`lab.py` loads real awards from `DATABASE_URL`, builds the leakage-safe training frame and compares
references and models with the production time split (train < T-6m, calibrate T-6m..T-3m, test T-3m..T),
stratified by how much award history each NSN had. Commands:

    python ml/research/lab.py --grid   # references for parts with history (comps grid search)
    python ml/research/lab.py --cold   # new parts, time split
    python ml/research/lab.py --lno    # new parts, leave-NSN-out on verified prices only

Data: a local live backfill (about 11,500 verified award prices across 840 NSNs; 11,000 NSNs described).
Production has roughly 5x more and has the final say: retraining promotes a model only when it beats the
active one on pinball loss over the same held-out window.

## Findings

1. **The headline error was mostly bad labels.** Award listings and record pages give a contract total;
   dividing it by the requested quantity is wrong when DLA awarded another quantity or the total spans
   several items (hex cap screws "at" $20,588 each, machine bolts at $3,571). These rows were most of the
   "new part" test set. `app/services/data_quality.py` now keeps such derived prices only when they agree
   (within 5x) with the NSN's verified prices; award labeling fetches the award PDF (exact line price) first.
   With the filter, new-part test rows fell from 48 to 5 on the local sample.
2. **Parts with price history (most live lines): prices are sticky.** Median absolute error of the
   predicted median was about 4-6%; the inflation-adjusted last award alone was as good or better on the
   median (4.6%). The model's value is the calibrated range and P(win) curve (better pinball loss and
   coverage). Recency/quantity-weighted "comps" references did not beat the last award. Shrinking the model's
   median toward the last award improves the median (4.5%) but costs pinball loss, so it is not used.
   Within-NSN quantity elasticity: -0.14 (`dibbs_ml.items.QTY_ELASTICITY`).
3. **New parts (no history) remain hard (median error about 80%).** Matching by item name is noise (DLA names
   are coarse: "GASKET" covers 872 lines and prices spanning 1,000x), and so is "same manufacturer + same
   name" (93% vs 88% for the FSC median). **Same part-number family** (manufacturer CAGE + part-number stem)
   carries real signal (49% vs 329% for the FSC median on those parts) but covers only 2-5% of new parts.
   v2 (`lgbm_quantile_v2`) uses part-family evidence as the cold-start reference when it exists, and the RFQ
   page shows those parts to the broker.

## What would move new-part accuracy

Technical characteristics: DLA's public FLIS / PUB LOG data (dimensions, material, MRC characteristics,
reference numbers) would let the model compare a new gasket with gaskets of the same size and material.
Until then the product's answer for new parts is the right one: show the evidence and get a supplier quote.

## Production run (2026-10-15, in-cluster, 49,434 verified awards across 4,874 NSNs; test = last 3 months, 1,982 rows)

| Protocol | Pinball | MdAPE | 80% coverage | MdAPE 5+ awards | MdAPE 1 award | MdAPE no history |
|---|---|---|---|---|---|---|
| A: train < T-6m, calibrate 3 months (old) | 0.2124 | 25.7% | 78% | 12.7% | 23.2% | 78.9% |
| **B: train < T-4m, calibrate 1 month (now)** | **0.2091** | 25.1% | 84% | 13.0% | 23.1% | 79.0% |
| C: train < T-3.5m, calibrate 2 weeks | 0.2112 | 25.7% | 88% | 12.7% | 24.3% | 79.2% |
| Last award + FSC residuals (baseline) | 0.2674 | 28.5% | 73% | 13.4% | 29.3% | 91.6% |

- At production scale the model clearly beats the last-award baseline (unlike the small local sample).
- B is now the evaluation protocol; the deployed model is refit up to T-1m and calibrated on the last month.
- Median shrinkage toward the last award: lambda 0.4 trades pinball 0.121 -> 0.129 for MdAPE 14.0% -> 13.8%: rejected.
- Capacity (15/31/63 leaves, 350-900 trees, min_child 25/100): no configuration better; defaults kept.
- New parts, held-out NSNs (2,047 rows; part-family evidence on 6.7%): cold model 83%; where evidence exists the
  evidence alone is 64% vs 88% (cold model) and 91% (model on top of evidence), so v2.2 anchors the median on the
  evidence and keeps the model's spread.
