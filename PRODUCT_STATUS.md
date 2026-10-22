# The Invisible Hand: product status and capabilities

*Snapshot as of 2026-10-22. Repo: `dibbs-bid-copilot` (GitHub `RodrigoAgAz/dibbs-bid-copilot`, branch `main`, last commit
`a50e92c`, 2026-10-15, "Pilot loop ... (step 7)"). Pricing engine **v13**, model code `lgbm_quantile_v2.2`, 10 database
migrations.*

---

## 1. What the product is, in one paragraph

A multi-tenant SaaS for small federal contractors ("brokers") who quote on Defense Logistics Agency (DLA) solicitations
posted on **DIBBS**. Every day it pulls the new RFQs in each firm's supply classes (FSCs), enriches each line with the part's
procurement history and approved sources, predicts the **distribution of the winning unit price**, estimates what the
broker would pay a supplier, emails suppliers for quotes and reads their replies with an LLM, and then **recommends a bid**
(or a "no bid" with reasons). The broker reviews it and submits on DIBBS themselves. **The app never submits bids.**

---

## 2. Current status at a glance

| Area | Status |
|---|---|
| Codebase | Feature-complete through the 7-step "make the maths a real value add" plan (steps 1–7 all committed). Local `main` is in sync with `origin/main`; only `data/` (local DB + scratch exports) is untracked. |
| Deployment | Built for **Railway** (6 services: backend, worker, scheduler, frontend, Postgres, Redis). The decisions log documents it running in **live mode** on real DIBBS data (live QA pass on 2026-10-15 with ~4,300 open lines). I did not check whether it is up right now. |
| Sign-in | **Development mode.** The sign-up and password pages are switched off. Signed-out visitors either get a simple username login (`SIMPLE_LOGIN_*`, signs in as the Atlas demo owner) or land in the read-only demo through `/enter`. `NEXT_PUBLIC_AUTH_PAGES=true` brings the real sign-up/login back. |
| Real-world integrations | DIBBS scraping, USAspending and BLS PPI are **verified**. Resend email, Anthropic LLM, Twilio + ElevenLabs voice, SAM.gov and Apify are **built from docs and tested against mocks only**, not against the live services. |
| Proof of value | **Not yet proven.** The pilot loop (step 7) exists to measure profit per broker-hour against a firm's own process, but **no real firm's bids, costs or outcomes have been imported yet**. |
| Model accuracy (real data) | On real awards: median absolute error of the predicted median is ~15–17%, **no better than "last award adjusted for inflation"**. Range coverage is good (80%/90% ranges hold ~76–84% / ~87–89%). New parts with no history are much worse (median error around 64% with part-family evidence, worse without). |
| Tests | ~26 test files and ~180 test functions across backend + ML, plus Vitest unit tests and one Playwright end-to-end flow. |

---

## 3. Everything a user can do

### 3.1 Roles
- **Owner**: everything, including settings, team invites and removing members.
- **Member**: works the lines, suppliers and quotes.
- **Viewer** (read-only): any non-GET request is refused (HTTP 403). The public demo session is a viewer.
- **Superadmin**: the `/admin` page (platform-wide ops, models, review queue).

### 3.2 Pages

| Page | What it does |
|---|---|
| **Landing** `/` (public) | Live stats from the database: open RFQ lines tracked, NSNs with price history, award prices on file, newest DIBBS posting, "winning price inside the 80% / 90% range". Live top-three opportunities from the public demo. "How it works", "Measured, not promised". |
| **Today / Home** `/dashboard` | A navigation hub, then "Where to start": the most pressing opportunities in plain language, plus the **portfolio** view (open book against working capital, award capacity, sourcing hours and supplier concentration, with notes on which lines are held back and why). |
| **Lines** `/lines` | Every open, bid-worthy line in the firm's watchlist. Filters: FSC, due within N days, quote status, search. Sorts: "best bets" (quoted lines by expected profit, then unquoted lines by triage score, then by order size), plus others. Badges for confidence, "Quote first", demo lines. CSV export. |
| **RFQ detail** `/rfq/[solicitation]` | The main working page. Header facts (buyer, buyer email, NAICS, set-aside, FOB, inspection, first article, flags such as fast award/AIDC). **Recommendation** (recommended bid, conservative/aggressive alternatives, or no-bid with reasons). **Why this price** and **Price story** (plain-language explanation). **P(win) vs bid** curve. **Landed cost per unit** waterfall. **Procurement history** table. **Most similar past awards** / **Similar parts DLA bought**. **Who wins this NSN** (manufacturers vs resellers, incumbents, how far under the last price). DLA evaluation notes. Sensitivity table (opens when the decision is fragile). Triage factors for unquoted lines. **Suppliers & quotes** (draft/send RFQ emails, simulated replies in demo, manual quote entry, review/edit parsed quotes with evidence badges, AI call button). **Decision history** (every snapshot). Record your **actual bid** and **actual cost**. Recompute button. |
| **History** `/history` | Tracked lines, win rate, "recommendation would win", median price error, winner inside the 80% range, left on the table, realized margin vs priced-for margin. **Pilot section**: lines compared, your profit/hour, the tool's profit/hour, increment, regret, by confidence tier, caveats. |
| **Suppliers** `/suppliers` | The firm's supplier book: company, CAGE, contact, quotes email, phone, timezone, FSCs it's saved for, consent to AI calls. Add/edit/delete. |
| **Settings** `/settings` | FSC watchlist; set-aside eligibility; cost inputs; margins and limits; portfolio and risk; supplier outreach; notifications; team (see §6 for every field). |
| **Admin** `/admin` (superadmin) | Organizations, solicitations, award rows, connector and provider status, ingestion runs, model runs (accuracy on the test window, quantile coverage, P(win) calibration, bidding backtest), activate a model, trigger ingest/labels/train/follow-ups, global quote review queue (correct unit price), download reports. |
| Auth pages | Login, signup, forgot/reset password, accept invite (currently mostly redirected, see §2). |
| `/terms`, `/privacy` | Describe what the product actually does. Not reviewed by a lawyer. |

### 3.3 Things the product does for the user automatically
- Pulls new RFQs every 2 hours (06:00–20:00 ET) and prices them.
- Re-prices open lines when new award history arrives for their NSN.
- Recomputes a recommendation the moment a supplier quote comes in.
- Sends supplier follow-ups hourly (when emails are being sent).
- Sends a **daily digest email** on weekdays at 09:00 ET: new profitable lines, lines closing within 2 days with no recorded bid, quotes waiting for review. Users can opt out. It is never sent to demo or internal orgs, or while email goes to the console outbox.
- Auto-sends RFQ emails to suppliers **only** if the org turned on "Auto-send RFQ emails". Otherwise drafts are created on demand and nothing is sent without a click.

---

## 4. Data sources: where everything comes from

### 4.1 Public data (shared across all tenants)

| Source | What we take | How | Status |
|---|---|---|---|
| **DIBBS RFQ index** `inYYMMDD.txt` (dibbs2 host) | Every RFQ line: solicitation, NSN, PR, return-by date, quantity, unit of issue, nomenclature, buyer code, AMSC, set-aside | Daily file, fixed-width 140 chars. Only ~30 days are kept online. | Verified |
| **DIBBS batch-quote zip** `bqYYMMDD.zip` | Per-CLIN detail: solicitation type (fast auto-eval / auto-eval / AIDC), FOB, inspection point, delivery days, AIDC terms, Buy American / trade flags, item description indicator (approved source, spec, QPL, source-controlled), higher-level quality; plus the `as` file of **approved sources** (NSN, CAGE, part number) | Daily zip, 121-column CSV | Verified |
| **DIBBS RFQ record page** | Status (Open/Awarded), issue/return dates, flags (fast award, AIDC, set-aside), tech-docs presence, **awardee + contract + total** once awarded | HTML page per solicitation | Verified |
| **DIBBS RFQ PDF** (SF-18) | Buyer name/code/phone/email, NAICS, delivery days, FOB/inspection per CLIN, packaging, first article, approved sources with part numbers, spec/drawing refs, and the **procurement history table** (CAGE, contract, qty, unit cost, award date, often 10–20+ years back) | PDF per solicitation, text extracted with pdfplumber. Automated (T/U) PDFs have history; manual (Q) PDFs don't. | Verified |
| **DIBBS NSN page** | AMSC, approved-source table, open solicitations for the NSN | HTML | Verified |
| **DIBBS award listings / searches** | Award rows by posting date (~45 days kept) and by NSN search | HTML GridView, postback paging | Verified |
| **DIBBS award package view + award PDF** | Awardee company name; **line-level quantity and unit price** (three schedule formats parsed) | HTML + PDF | Verified |
| **DIBBS CAGE pop-up** | Company name and address for a CAGE | HTML behind the DoD banner | Verified |
| **USAspending API** | Recipient UEI, business size categories, **number of offers received**, solicitation link | JSON API, ~90-day publication lag, totals only | Verified against fixtures; optional cross-check, not used for price labels |
| **SAM.gov Entity API** | UEI, registration details | Needs `SAM_API_KEY` | **Unverified** (no key during build) |
| **BLS PPI via FRED** | Producer price indices to inflation-adjust old awards (fasteners → WPU1081, rubber → WPU0713, wire → WPU1026, other metals → WPU10) | Committed CSV `ml/data/ppi.csv`, refreshed with `ml/scripts/refresh_ppi.py` | Verified |
| **Apify** (fallback) | Alternative RFQ index source | Needs `APIFY_TOKEN` | **Unverified** |
| **FLIS / PUB LOG characteristics** | Per-NSN technical characteristics (material, thread size...) used to find "similar parts" | Manual file import (`import-characteristics`). dla.mil blocks non-browser downloads, so the real format is **unverified** and **no data is loaded yet**. | Built, unused |
| **DLA Master Solicitation Part I** | The automated-award evaluation rules encoded in `services/evaluation.py` | Read by hand from a PTAC deck of rev 46, cross-checked against rev 104 snippets. **A human should re-read rev 105 §3.** | Encoded |

**Not used, on purpose:** cage.dla.mil (needs accepting a separate usage agreement), JCP/cFolders/TDMT technical data,
DIBBS quote forms or anything behind a login, the 400 MB `ca*.zip` daily PDF bundles.

**Access rules every connector follows:** accept the DoD consent banner (form POST, or Playwright as a fallback; only the
`dw` cookie is needed), at most 1 request/second per host (shared across processes via Redis), exponential backoff, an
identifying `User-Agent`, raw responses cached in `data/raw/<source>/<date>/`, idempotent upserts. Every connector has an
offline **fixture** twin built from real captured samples (`backend/tests/fixtures/`).

### 4.2 Per-firm data (private to each tenant)

| Data | Where it comes from |
|---|---|
| FSC watchlist, set-asides, cost inputs, limits | Settings page |
| Supplier contacts | Suppliers page; the RFQ page also proposes candidates (see §5.5) |
| Supplier quotes | Inbound email replies parsed by the LLM, simulated replies (demo), manual entry, AI voice calls |
| Distributor price lists | CLI `import-catalog` (per org, per supplier CAGE) |
| Actual bids and costs | RFQ page buttons, or CLI `import-bids` / `import-costs` |
| Outcomes (won/lost) | Award labeling from DIBBS; or CLI `import-outcomes` (needs the org's CAGE) |

Nothing is pooled across organizations. One firm's quotes never inform another firm's cost estimates.

### 4.3 Demo data
- `make seed` / `python dev.py seed` creates two demo orgs (**Atlas Federal Supply**, **Bluewater Parts Co.**), synthetic
  solicitations with fictional office codes `SPEZZ1..7` (they can't collide with real ones), synthetic award history and a
  trained model. Live deployments hide the synthetic lines.
- The **demo refresher** (every 2 h) picks up to 12 *real* open lines for demo orgs and attaches simulated quotes from six
  fictional "(demo)" suppliers (CAGE `ZZD01`–`ZZD06`, example.com addresses), so visitors see the whole sourcing loop on
  live RFQs.

---

## 5. How it works, step by step

### 5.1 Ingest (every 2 h, 06:00–20:00 ET)
1. Accept the DoD banner, read the "recent RFQs" page, download each new `in*.txt` and `bq*.zip`.
2. Upsert lines in any FSC that some org watches (key: solicitation + purchase request; a newer post date wins).
   Part-number-only lines (item type 2) are skipped; local stock numbers are kept.
3. Fetch record pages + PDFs for new solicitations (up to `DETAIL_FETCH_LIMIT`, default 600 per run). The PDF gives the
   procurement history, approved sources and terms.
4. Predict the winning-price distribution, then refresh every matching org's recommendations.
5. Re-price open lines whose NSN gained award rows since they were last priced.
6. A newly watched FSC loads its last 10 days of RFQs immediately.

### 5.2 Award labeling (07:30 ET daily) and backfill (01:00 ET nightly)
- **Labeling** checks only closed solicitations, with backoff (every 2 days for 2 weeks after close, then 5, then 14, up
  to 75 days; max 400 record pages per run). Lines the broker bid on are checked first. It fetches the **award PDF first**
  (for the true unit price) and falls back to total ÷ quantity.
- **Nightly backfill** grows history: PDFs for NSNs not yet covered, 3 older award posting dates, 10 NSN searches.
  Everything is checkpointed and resumable. A full manual backfill: `make backfill FSC=... START=... END=today MODE=live`.
- **Data quality rule:** prices derived from contract totals are quarantined unless within 5× of the NSN's verified median.
  Only RFQ-PDF history and award PDFs count as verified.

### 5.3 Price prediction (the ML model)
- **Target:** log(winning unit price ÷ reference price). The reference is the inflation-adjusted last award, else the DoD
  standard price, else the FSC median over 36 months.
- **Features** use strictly earlier data only. One shared function serves training and inference, and a leakage test
  corrupts future rows to prove nothing leaks.
- **Model:** LightGBM quantile regression (11 quantiles) + split-conformal calibration. A separate FSC-level model handles
  cold-start NSNs (no prior award) with wider intervals.
- **New parts:** "part-number family" evidence (other NSNs in the same FSC with matching approved-source CAGE + part-number
  stem) becomes the median when present. Name-only and manufacturer-only similarity were tested and dropped (no signal).
- **Quantiles → full distribution** via PCHIP interpolation in normal-score space, giving P(win | bid) = P(W > bid).
- **Confidence tiers:** high (≥3 awards, tight range, last five awards within 2.5×), medium, low, none (no history →
  "Quote first").
- **Training** (Sundays 03:00 ET, or on a model-code change): train up to T−4 months, calibrate T−4..T−3, test the last 3
  months. Promote only if it beats the active model's held-out twin on pinball loss **and**, once there are ≥30 pilot lines,
  doesn't have >5% more regret on the firm's real lines. The deployed model is then refit up to T−1 month. Artifacts live in
  Postgres (`model_runs.artifact_blob`) so all services see the same model.
- With no trained model, a transparent lognormal heuristic (`heuristic_v0`) is used and labelled as such.

### 5.4 Cost before anyone has quoted (tenant cost model + triage)
- The **tenant cost model** estimates the supplier price from the first tier with enough data: this firm's quotes for this
  NSN → an imported catalog price for the exact approved part → the same manufacturer → the same FSC → all of the firm's
  quotes (as a ratio to the predicted winning price) → the org's `provisional_cost_fraction` (default 70%) as a **labelled
  assumption**. Each estimate shows its tier, count and basis.
- **Triage score** for unquoted lines = profit if sourced × P(a supplier price lands under target) × P(a candidate replies
  before the deadline, from the firm's own response rates) × P(a broker can win this part at all) − pursuit cost
  ($15 default). Every factor is shown on the RFQ page.
- **Contestability / competitor model:** from public awards, every repeat winner per FSC is profiled and classified as a
  manufacturer or reseller. P(winnable) = the part's reseller share of awards, shrunk toward the FSC share, floored at 5%.
  A competitor-based view of P(win) is **shown but not used** for the bid (it's biased low, since only winning prices are
  observed).

### 5.5 Sourcing (supplier quotes)
1. **Candidate suppliers**: approved sources for the NSN first, then suppliers the firm has quoted with before, then
   suppliers saved for this FSC.
2. **Drafts** are created from a strict template (no LLM cost). The LLM (Claude Sonnet 5.5) polishes a draft on demand or
   before an auto-send, and falls back to the template if it drops a required fact.
3. **Send** via Resend (or the console outbox locally). Reply-to is `quotes+<token>@<domain>`, so replies map back to the
   request.
4. **Inbound webhook** (HMAC-signed) receives replies, reads text and PDF attachments, and the LLM extracts a structured
   quote: price, **pricing unit and basis quantity**, part number, lead time, validity, freight, MOQ.
5. **Evidence states**: each field is marked confirmed / inferred / unknown. A quote that doesn't state a part number can't
   count as conforming on an approved-source-only line. A price per box with no pack size can't be used until a basis is set.
6. **Needs review** when extraction confidence < 0.6, the price is >5× off the NSN's history, or the part is non-conforming.
   Expired quotes and quotes whose lead time doesn't fit the delivery days are ignored for cost.
7. **Optional AI voice calls** (Twilio + ElevenLabs agent). Guardrails are enforced in code: global and org flags,
   landline or explicit consent only, Mon–Fri 09:00–17:00 supplier local time, at most one call per supplier per day, the
   agent discloses it's an AI and asks consent to record.

### 5.6 Landed cost
`landed = supplier unit price (normalized to the line's unit of issue) + freight + packaging + inspection allowance
(+ origin inspection extra) + first-article allowance + financing (price × cost of capital × payment days ÷ 365) +
overhead`. If the supplier MOQ exceeds the line quantity, the MOQ cost is spread over the quantity sold.

### 5.7 The recommendation (bidding engine v13)
- Picks the bid that maximizes **P(win | bid) × (bid × (1 − fulfilment loss) − landed cost) × quantity**, searching only
  bids with P(win) ≥ the org's minimum (default 20%).
- Capped at the band DLA's automated evaluation has actually awarded in that FSC (95th percentile of winning price ÷
  inflation-adjusted previous award on T/U buys, needs 30 awards).
- Also gives **conservative** (P(win) ≈ 70%) and **aggressive** (≈ 30%) alternatives.
- **Unquoted lines with history** get a provisional bid near the predicted median (P(win) ≥ 50%) and show "profit if
  sourced, if landed cost ≤ $X". They're **not ranked by expected profit** (that would just rank by order size).
- **Unquoted lines with no history** get **"Quote first"**: no bid, just the price range (hidden if wider than 20×).
- **No-bid reasons**: cost too high for the minimum margin, below minimum expected profit, over max line value, set-aside
  the firm isn't eligible for, above DLA's awarded band, etc.
- **DLA evaluation checks** on the chosen quote: alternate part number, FOB terms mismatch, +$2,500 origin-inspection
  factor, +$200 surplus factor, delivery feasibility warnings, first article and Q-type buys flagged as manually evaluated.
- **Sensitivity**: re-runs the engine for supplier +10%, competitors −5%, competitors +5%. Marked **fragile** if any
  scenario flips bid/no-bid or moves the bid by >15%.
- **Snapshots**: every decision-relevant change is saved (append-only), so History judges recommendations on what was shown
  *before* the award posted.

### 5.8 Portfolio limits
`GET /api/portfolio` sets the whole open book against the firm's limits: expected capital in flight vs working capital limit,
expected awards vs max open awards, sourcing hours vs weekly hours (best triage scores first), share of capital on one
supplier vs the max share. Limits **hold lines back with a plain-language note; they never delete them**. Assumes lines win
independently.

### 5.9 Measuring results (History, track record, pilot)
- **Track record** (landing + History) counts only predictions made **before** the award and since 2026-10-15, with
  verified winning prices only.
- **Outcomes**: "lost" only when the broker recorded a bid; otherwise "awarded" (to someone else).
- **Realized margin** comes only from an actual cost the firm recorded; the "priced-for" margin is shown separately.
- **Pilot loop** (step 7): import a firm's real bids, costs and outcomes; for every awarded line where the tool had decided
  in advance, compare the firm's profit, the tool's counterfactual profit and hindsight profit, per broker-hour. Three
  comparisons (head to head, head to head on real costs only, tool as a policy over all lines), by FSC and confidence tier.
  Output on the History page, `GET /api/history/pilot`, and `ml/reports/pilot_<org>.md`. Below 30 lines it says "too early
  to call". Known bias: runs **against** the tool (a counterfactual bid above the firm's winning bid is scored as a loss).

---

## 6. Every setting a firm can change

| Group | Settings (defaults where notable) |
|---|---|
| Watchlist | FSC watchlist; set-aside eligibility (unrestricted is always eligible) |
| Cost inputs | Overhead %, packaging per line, payment terms (days), cost of capital %, default freight per line, inspection allowance per line, origin inspection extra, first article allowance, provisional cost estimate (70% of predicted median), handling days (3) |
| Margins & limits | Minimum margin %, max line value, minimum expected profit ($25), minimum chance to win (20%), effort per line pursued ($15), fulfilment losses (2%) |
| Portfolio & risk | Working capital limit, awards you can fulfil at once, sourcing hours per week, hours per line pursued, max share on one supplier |
| Supplier outreach | Auto-send RFQ emails (off), AI voice calls (off), sender name, signature |
| Notifications | Daily digest email (per user) |
| Team | Members, roles, invitations (owners only) |

---

## 7. API surface (all under the Next.js origin; `/api/*` is proxied to FastAPI)

| Area | Endpoints |
|---|---|
| Auth `/api/auth` | `signup`, `login`, `options`, `demo` (one-click read-only), `refresh`, `logout`, `me`, `me/preferences`, `forgot-password`, `reset-password`, `accept-invite` |
| Org | `GET/PUT /api/settings`, `GET /api/org/members`, `DELETE /api/org/members/{id}`, `GET/POST /api/org/invitations` |
| Dashboard | `GET /api/dashboard`, `GET /api/portfolio` |
| RFQ `/api/rfq` | `GET /{solicitation}`, `POST /lines/{id}/actual-bid`, `POST /lines/{id}/actual-cost`, `GET /lines/{id}/snapshots`, `POST /lines/{id}/refresh` |
| Quotes | `POST /api/rfq/lines/{id}/quote-requests` (+ `/draft`), `PATCH /api/quote-requests/{id}`, `POST .../draft`, `.../send`, `.../simulate-reply` (demo only), `.../quotes` (manual entry), `PATCH /api/supplier-quotes/{id}`, `GET /api/review-queue`, `POST /api/rfq/lines/{id}/call` |
| Suppliers | `GET/POST /api/suppliers`, `GET/PATCH/DELETE /api/suppliers/{id}` |
| History | `GET /api/history`, `GET /api/history/pilot` |
| Public | `GET /api/public/top-lines` (only with `PUBLIC_DEMO`), `GET /api/health`, `/api/health/ready`, `/api/health/data` (data freshness, no tenant data) |
| Admin `/api/admin` | `overview`, `ingestion-runs`, `model-runs`, `model-runs/{id}/activate`, `ingest`, `labels`, `train`, `followups`, `review-queue`, `supplier-quotes/{id}/review`, `reports/{name}` |
| Webhooks `/api/webhooks` | `inbound-email` (HMAC), `elevenlabs` (HMAC), `twilio/status` (Twilio signature) |

---

## 8. Background jobs (scheduler → Redis/RQ → worker)

| Job | When (ET) | Queue |
|---|---|---|
| Daily ingest | Every 2 h, 06:00–20:00 | default |
| Demo refresh | Every 2 h at :30, 06:30–20:30 | default |
| Award labeling | 07:30 daily | bulk |
| Nightly backfill | 01:00 daily (live mode) | bulk |
| Supplier follow-ups | Hourly at :15 | default |
| Ops check (stale data alerts, re-queue engine refresh / retrain after deploys) | Hourly at :45 | default |
| Daily digest | Mon–Fri 09:00 | default |
| Retrain | Sunday 03:00 | bulk |

Two worker pools so long jobs never block the ingest. The scheduler skips a job already queued or running. A rules change
(`ENGINE_VERSION`) recomputes every open line once; a model-code change (`MODEL_CODE_VERSION`) queues one retrain. Ops alerts
email `OPS_ALERT_EMAIL` (at most every 12 h) when ingest is >14 h old, labeling >30 h, or the newest index file >4 days.

---

## 9. CLI (`python -m app.cli <command>`)

`migrate`, `seed`, `bootstrap`, `backfill`, `ingest`, `labels`, `train`, `fsc-report`, `export-openapi`,
`import-characteristics <file>`, `import-catalog <file> --org --supplier [--manufacturer]`, `import-bids <file> --org`,
`import-costs <file> --org`, `import-outcomes <file> --org`, `pilot-report --org`, `create-superadmin <email>`.
Research scripts: `ml/research/{lab,cost_lab,competitor_lab,coverage_lab}.py`.

---

## 10. Tech stack and architecture

- **Backend:** FastAPI, Python 3.12, SQLAlchemy 2, Alembic, Pydantic v2, PostgreSQL 16, Redis + RQ + APScheduler.
- **ML:** `ml/dibbs_ml` (pure Python, no DB models): features, LightGBM quantiles, conformal, CDF, bidding engine, cost model,
  competitors, evaluation, backtest, pilot, reports.
- **Frontend:** Next.js 15 App Router, TypeScript, Tailwind 3.4 with shadcn-style components, TanStack Query, Recharts, typed
  OpenAPI client.
- **External:** Anthropic API (Claude Sonnet 5.5) for email drafting and quote extraction; Resend for email; Twilio +
  ElevenLabs for voice (OpenAI Whisper only as a transcript fallback).
- **Tenancy:** one dependency (`get_tenant`) binds each request to the user's org; SQLAlchemy hooks filter every query on
  tenant-owned tables and refuse cross-org writes. Public DIBBS data is shared.
- **Security:** argon2 passwords, 15-min JWT + rotating 30-day refresh token in httpOnly cookies, CSRF double-submit,
  rate limits on login/signup/reset.
- **Offline twins** for every external call (fixture connectors, mock LLM, console email, mock voice), so tests and the demo
  never touch the network.
- **Local dev without Docker:** `python dev.py setup | seed | api | web | test` with embedded Postgres; jobs run in-process.

**Database tables (25):** solicitations, solicitation_lines, nsn_items, item_characteristics, approved_sources,
award_history, companies, model_runs, price_predictions, recommendations, recommendation_snapshots, ingestion_runs,
ingest_checkpoints, supplier_contacts, quote_requests, supplier_quotes, catalog_prices, email_messages, call_logs,
organizations, users, org_settings, refresh_tokens, password_reset_tokens, invitations.

---

## 11. Known limitations and open items

**Things that aren't proven yet**
- **No pilot data.** Nothing yet shows the tool makes a broker more money per hour than their own process. That's the most
  important open item.
- The model's point estimate is **no better than the inflation-adjusted last award**. Its value is the calibrated ranges and
  the win-probability curve. Cold-start parts remain hard.
- Cost-model and competitor-model research has only run on simulated demo quotes locally.

**Modelling assumptions**
- P(win) ≈ P(winning price > bid), because DIBBS only shows the winner. Price-only is correct for automated buys per DLA's
  rules but approximate for manual (Q) and first-article buys.
- Portfolio maths assumes lines win independently.
- The pilot's win rule is biased against the tool (stated in every report).

**Integrations not tested live:** Resend, inbound email adapters, Anthropic (tested against a fake SDK), Twilio, ElevenLabs,
SAM.gov, Apify. PUB LOG characteristics format is unverified and no data is loaded.

**Data coverage:** DIBBS keeps ~30 days of RFQ files and ~45 days of award listings; deeper history only comes from RFQ PDFs.
Multi-NSN PDFs were never observed. Set-aside isn't in the PDF text, so the index code is used.

**Business / ops**
- Sign-up and normal login are turned off for development; switch them back on before onboarding real customers.
- `/terms` and `/privacy` need a lawyer's review before charging anyone.
- DLA evaluation rules were read from an older revision; someone should re-read the current Master Solicitation (rev 105)
  Part I §3.
- Docker Compose and the Makefile were never run on the build machine (verified through `dev.py` instead).
- Approved-source logic treats AMSC B/C/D/H/P/R and source-controlled items as approved-source-only; Buy American, AS9100,
  packaging and first-article requirements still need the broker's judgment.

---

## 12. Where to read more

- [README.md](README.md): setup, architecture diagram, environment variables
- [CLAUDE.md](CLAUDE.md): code map and conventions
- [DECISIONS.md](DECISIONS.md): every judgment call, newest last (engine versions 3–13, pilot loop)
- [docs/data_sources.md](docs/data_sources.md): observed DIBBS formats field by field
- [docs/deploy_railway.md](docs/deploy_railway.md): the six Railway services and their variables
- [ml/research/README.md](ml/research/README.md): pricing research and lab results
- [ml/reports/live_snapshot_2026-10-09/](ml/reports/live_snapshot_2026-10-09/): metrics from the bounded live backfill
