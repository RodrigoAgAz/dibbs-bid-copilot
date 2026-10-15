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

## Cost model (2026-10-15): how predictable is a broker's supplier price before asking?

`cost_lab.py` loads every usable quote an organization received (normalized to the line's unit of issue, the way
`app/services/cost_model.py` does for live pricing), scores each quote from the organization's **other** quotes
(leave-one-out, `dibbs_ml.cost_model.TenantCostModel`) and reports the error by the tier the estimate came from:

| Tier | Uses | Minimum observations |
|---|---|---|
| nsn | the tenant's quotes for this NSN, quantity-adjusted with the measured elasticity (−0.14) | 1 |
| cage | quotes for other parts by the same approved-source manufacturer, as a ratio to the predicted winning price | 2 |
| fsc | quotes in the same supply class, as a ratio | 3 |
| org | all the tenant's quotes, as a ratio | 5 |
| prior | the org's provisional cost fraction (default 70%), spread σ = 0.45 | — |

    DATABASE_URL=... python ml/research/cost_lab.py

Local run on the demo organizations (quotes **simulated** by the demo refresher at roughly 70% of the predicted price,
so this shows that the pipeline works, not what real suppliers do):

| Org | Quotes | Tier | n | MdAPE | within 25% | 80% coverage | flat 70% MdAPE |
|---|---|---|---|---|---|---|---|
| Atlas (demo) | 15 | nsn | 9 | 10% | 89% | 78% | 5% |
| Atlas (demo) | 15 | cage | 2 | 21% | 50% | 50% | 23% |
| Atlas (demo) | 15 | org | 4 | 30% | 50% | 75% | 27% |
| Bluewater (demo) | 11 | nsn | 6 | 4% | 67% | 67% | 17% |
| Bluewater (demo) | 11 | org | 5 | 34% | 40% | 100% | 26% |

What to expect on real tenants: the nsn tier should beat the flat assumption decisively (a supplier re-quoting a part
it quoted last quarter), the cage and fsc tiers are where a tenant's structural advantage (its distributor terms)
shows up, and the prior is what a brand-new tenant sees until it has quoted five lines. Re-run per pilot customer and
replace this table; the tiers' minimum counts and spread floors are the knobs.

## Competitors and contestability (2026-10-15)

DIBBS names the winner of every award, so `dibbs_ml.competitors` profiles each repeat winner per supply class (wins,
parts, recency, and the ratio of its winning price to the inflation-adjusted previous award) and classifies it as a
manufacturer (an approved source for some NSN, or named as the manufacturer on an RFQ) or a reseller. Two uses:

* **Contestability** P(a broker can win the line) = the part's reseller share of awards, shrunk toward the supply
  class's share with three awards' worth of prior. It feeds the triage score and is shown on the RFQ page.
* **Competitor view of P(win)** = prod over likely bidders (1 − P(bids) × P(their price < bid)). Only winning prices
  are observed, never losing bids, so this runs low; it is shown beside the calibrated price model, never used instead.

`competitor_lab.py` walks each class's awards in time order and scores the shrunk reseller share against the constant
class share (Brier, lower is better). Local run on the **synthetic** demo awards (resellers win about 96% of them by
construction, so the margins are small):

| FSC | Awards scored | Reseller share | Brier (part history) | Brier (FSC share only) |
|---|---|---|---|---|
| 4730 | 660 | 97% | 0.018 | 0.025 |
| 5305 | 1,103 | 96% | 0.024 | 0.037 |
| 5306 | 755 | 95% | 0.023 | 0.044 |
| 5310 | 828 | 98% | 0.016 | 0.017 |
| 5330 | 763 | 98% | 0.019 | 0.022 |
| 5340 | 555 | 97% | 0.020 | 0.025 |
| 6145 | 647 | 96% | 0.030 | 0.039 |

On real DIBBS awards the interesting classes are the ones where manufacturers win a material share: that is where the
part's own history separates broker-winnable lines from the rest. Re-run in production and keep the table.

## Enrichment and coverage (2026-10-15)

Two enrichment paths were added behind measured gates rather than assumed value:

* **FLIS / PUB LOG characteristics** (`item_characteristics`, `python -m app.cli import-characteristics`): same-name
  parts agreeing on ≥ 2 characteristics become similar-item neighbours ("matching characteristics"). Their price
  signal must be measured with `lab.py --cold` once real PUB LOG data is loaded; the live export format is UNVERIFIED
  (docs/data_sources.md §13).
* **Distributor price lists** (`catalog_prices`, `python -m app.cli import-catalog`): an imported price for the exact
  approved part number is the cost model's "catalog" tier. `coverage_lab.py --org <id>` measures, before any feed is
  integrated, what share of the organization's open lines a price list would touch, by FSC, alongside how many lines
  have an approved part number at all and a candidate supplier with an email address.

Local run on the Atlas demo organization (no catalog imported, no characteristics loaded): see the table printed by
`coverage_lab.py`; the useful numbers appear once a pilot customer's price lists are imported.
