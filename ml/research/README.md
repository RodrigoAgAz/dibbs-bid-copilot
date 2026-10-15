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
