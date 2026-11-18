# Decisions log

Ambiguities resolved during the build, and deviations from the build prompt. Newest last.

## Build environment
- **No Docker, `make`, Postgres or Redis on the build machine (Windows 11).** The Docker Compose
  stack and Makefile are written for the target environment, but they could **not be executed during
  the build**. To still verify everything on real PostgreSQL, the test suite and `dev.py` use an
  embedded PostgreSQL 16.2 via the `pgserver` pip package. `dev.py` mirrors every Makefile target.
- RQ workers need `fork` (not available on Windows). Locally, when `REDIS_URL` is empty or Redis is
  unreachable, jobs run in a background thread in-process (`app/jobs/queue.py`). In Docker the
  `worker` service runs RQ normally.

## Stack
- Tailwind **v3.4** (not v4) with hand-written shadcn/ui-style components (button, card, table, badge,
  switch, tooltip, ...). Same API and look as shadcn/ui, without needing the shadcn CLI or network at build.
- System font stack instead of `next/font/google`, so builds don't download fonts.
- The browser talks only to the Next.js origin; `/api/*` is rewritten to FastAPI. Auth cookies are
  therefore first-party. CORS is still restricted to `FRONTEND_ORIGIN` for direct API access.
- Typed client: `openapi-typescript` (types) + `openapi-fetch` (runtime).

## Data model
- Arrays (`fsc_watchlist[]`, `set_aside_eligibility[]`, `certs_offered[]`) use a portable `StringList`
  type: `TEXT[]` on Postgres, JSON on SQLite. JSON columns are `JSONB` on Postgres.
- Enums are stored as VARCHAR + CHECK (`native_enum=False`) so migrations stay simple.
- Extra tables beyond the minimum schema: `refresh_tokens`, `password_reset_tokens`, `invitations`,
  `email_messages` (outbound + inbound mail, the console provider's outbox), `ingest_checkpoints`
  (resumable backfill/ingest).
- Extra `org_settings` cost inputs used by the landed-cost formula: `default_freight_per_line`,
  `inspection_allowance_per_line`, `origin_inspection_allowance_per_line` (added when inspection is at
  origin), `first_article_allowance_per_line`, `provisional_cost_fraction` (landed cost estimate when no
  quote exists: fraction of predicted median winning price, default 0.70), `min_expected_profit`
  (no-bid floor, default $25), plus `auto_send_rfq`, `voice_enabled`, email sender name/signature.
- `organization_id` is also stored on `supplier_quotes`, `call_logs` and `email_messages`
  (denormalized from `quote_requests`) so the single tenancy layer can filter them uniformly.
- `supplier_contacts.fsc_tags` marks suppliers "saved for this FSC" (third tier of candidate suppliers).
- `users.is_superadmin` gates `/admin`. `python -m app.cli create-superadmin <email>` creates one.

## Auth & security
- Access JWT 15 min, refresh token 30 days (opaque, SHA-256 hashed in DB, rotated on every use,
  all revoked on password reset). CSRF: double-submit cookie (`csrf_token` cookie + `x-csrf-token`
  header) on cookie-authenticated mutating requests; auth endpoints that create a session are exempt.
- `COOKIE_SECURE` defaults to `false` in `.env.example` for http://localhost demos; set `true` in production.
- Rate limits: login 10 per 5 min per email (30 per IP), password-reset 5 per hour, signup 5 per hour.
  Redis-backed when available, in-memory otherwise.
- Tenancy enforced at the SQLAlchemy session level (`with_loader_criteria` on every tenant-owned
  class + a `before_flush` guard), activated by the single `get_tenant` dependency.

## Data sources (see docs/data_sources.md for the observed formats)
- Discovery ran against the live public site on 2026-10-09 (about 200 requests at 1 req/s, no blocking).
  All parsers are written against those real samples in `backend/tests/fixtures/` (CRLF preserved via
  `.gitattributes`).
- **DoD banner**: the default `DIBBS_BANNER_MODE=browser` uses Playwright as specified and copies cookies into
  httpx; it falls back automatically to a plain form POST (verified to work; only the `dw` cookie is needed).
  `form` avoids shipping Chromium.
- RFQ **record pages** and **PDFs** are both fetched for new solicitations (bounded by `DETAIL_FETCH_LIMIT`).
  The PDF URL is derived from the solicitation number (`/Downloads/RFQ/<last char>/<SOL>.PDF`).
- Lines whose index "NSN" is a part number (item type 2) are skipped (no FSC). Local stock numbers with
  letters (`4730LLQJ07784`) are kept, since they still carry an FSC.
- **Approved-source-only** = AMSC in {B, C, D, H, P, R} or bq item-description indicator `B` (source
  controlled). That's a conservative reading of the DoD AMSC definitions; brokers can still quote others.
- **No public procurement-history page exists**: history comes from automated-RFQ PDFs (10+ years deep),
  award listings (about 45 days) and NSN award searches. The backfill fetches one PDF per NSN (preferring T/U
  solicitations). DIBBS keeps RFQ index files for about 30 days only, so the 2-year history target is met
  through PDF history, not by walking old RFQs.
- Award listings give totals, not quantities. Unit price = total / our line quantity for single-line
  solicitations, otherwise it comes from the award PDF (three schedule formats parsed). AIDC basics
  (quantity 0, contract max) are skipped.
- **cage.dla.mil is not used**: every page requires accepting a separate usage agreement, which the build
  rules didn't cover. Company names come from the DIBBS CAGE pop-up, award package pages and RFQ PDFs.
  The SAM.gov Entity API client is implemented from public docs only (UNVERIFIED, no key).
- **Apify fallback** (UNVERIFIED, no token): maps common field spellings of a DIBBS RFQ actor's output to
  index lines; everything else still comes from DIBBS.
- USAspending: client verified against fixtures (about a 90-day publication lag, totals only). It's used as
  an optional cross-check, not for labels.
- Inflation: real BLS PPI series via FRED (`ml/data/ppi.csv`, `ml/scripts/refresh_ppi.py`). Mapping:
  5305/5306/5310 -> WPU1081 (bolts, nuts, screws, rivets, washers), 5330 -> WPU0713 (misc. rubber
  products), 6145 -> WPU1026 (nonferrous wire and cable), 5340/4730/other -> WPU10 (metals and metal products).

## Pricing model
- Target `y = log(W / ref)`; ref = inflation-adjusted last award, else DoD standard price, else FSC median
  (36 months, strictly before the as-of month). As-of = solicitation issue date, or award date minus 21 days
  when the solicitation is unknown (procurement-history rows).
- One feature function serves both training and inference (`dibbs_ml.features.compute_features`). It filters
  history to `< as_of` itself, and `ml/tests/test_leakage.py` corrupts all future rows to prove nothing leaks.
- Cold start (no prior award): a separate "FSC-level" LightGBM trained on all rows without NSN-history
  features, relative to the DoD/FSC reference, with group-conditional conformal plus 1.25x interval widening.
  "Low data" badge when there are fewer than 3 prior awards.
- Quantiles -> CDF: PCHIP in (z = Phi^-1(p), log price) space, linear tails in z. That's exact for lognormal
  quantiles, which the recommendation-math tests rely on.
- If calendar windows (T-6m / T-3m) have fewer than 30 rows, the split falls back to time-ordered 60/20/20 and
  the report says so.
- Promotion: the new model must beat the active model's mean pinball loss on the *same* new test window.
- Backtest landed-cost assumption as specified (W x U(0.6, 0.95)). The report explains that this hands
  "cost + 15%" near-oracle information, so `last_minus_2pct` is the fairer naive benchmark.
- With no trained model, predictions use a transparent heuristic (lognormal around the reference, sigma 0.25,
  or 0.55 cold) labeled `heuristic_v0`.

## Sourcing, email, LLM, voice
- LLM default model `claude-sonnet-5-5` (from `LLM_MODEL`, as specified). Calls use JSON-schema structured
  outputs (`output_config.format`), are re-validated with Pydantic, and enable the server-side refusal fallback
  (`LLM_REFUSAL_FALLBACK`). Email drafts are checked to still contain the key facts, otherwise the strict
  template is used.
- Draft quote requests created during ingest use the deterministic template (no LLM cost for hundreds of
  drafts). The LLM polishes a draft on demand (`/draft`) or before an auto-send.
- Inbound email webhook: generic JSON payload with an HMAC-SHA256 hex signature (`X-Inbound-Signature`),
  so any inbound-parse service can be adapted to it. Replies map to requests by the
  `quotes+<token>@<domain>` address. PDF/text attachments are read.
- Needs review: extraction confidence below 0.6, price more than 5x away from the NSN's historical median,
  or a non-conforming part number on an approved-source-only line.
- `simulate-reply` endpoint (demo mode only) runs a typed supplier reply through the same inbound pipeline.
- Voice guardrails in code: global flag + org flag, landline or explicit consent, Mon-Fri 09:00-17:00 in the
  supplier's timezone (contact override, else company state, else Eastern), at most one call per supplier per
  org per local day. ElevenLabs' Twilio outbound-call API places the call; transcripts arrive via the
  post-call webhook (HMAC `ElevenLabs-Signature`). Twilio status callbacks are verified with
  `X-Twilio-Signature`.
- Landed-cost MOQ handling: when the supplier MOQ exceeds the line quantity, the MOQ cost is spread over the
  quantity sold (conservative).

## Recommendations
- An organization only gets recommendations for lines in its FSC watchlist. A set-aside the org isn't
  eligible for adds a no-bid reason (beyond the spec's four rules). "Unrestricted" set-asides are always eligible.
- Signup bootstraps recommendations inline when there are at most 150 open lines, otherwise as a background job.
- Organizations with `plan = internal` (the superadmin's platform org) never get recommendations.

## Demo data
- Synthetic solicitations use the fictional issuing offices `SPEZZ1..7`, so they can never collide with real
  DIBBS numbers. They are excluded from the FSC report, and the UI marks them as demo. Demo org CAGE codes
  are 9ZDM1/9ZDM2. Synthetic set-aside mix and quantities follow the real index files.
- Every row in an RFQ PDF's procurement-history table is stored, even rows older than the backfill
  START date (some go back to 1988). They're free, improve NSN features, and START only bounds the
  walk over award listings and searches. PPI adjustment before 2015 uses the first available index value.
- The backfill has run-size limits (`--max-pdfs`, `--max-award-dates`, `--max-award-pages`,
  `--max-nsn-searches`). DIBBS award listing pages take about 30 s each to serve, so unbounded walks run for hours.

## Deployment (Railway)
- Model artifacts are also stored in Postgres (`model_runs.artifact_blob`, migration 0002). Services on
  Railway don't share a filesystem, so a model trained by the worker must reach the API via the database.
  The local file is a cache.
- Hosted Postgres URLs (`postgres://` / `postgresql://`) are rewritten to `postgresql+psycopg://` in settings.
- The backend serves via `python -m app.serve`, which binds one dual-stack socket on `$PORT` and passes it to
  uvicorn. `uvicorn --host ::` is IPv6-only, because asyncio sets IPV6_V6ONLY, so Railway's IPv4 health check
  never reached it. The frontend reaches
  it through the private domain (`BACKEND_URL`, baked into the Next.js rewrite at build time).
- `python -m app.cli bootstrap` is the container start step: migrations, plus the demo seed when
  `AUTO_SEED=true` and the database has no organizations.
- No `railway.json` in the repo: Railway deprecated config-as-code (the API rejects config-file paths), and a
  root `railway.json` would also apply its HTTP health check to the worker and scheduler. Build and start
  settings are per-service instead (see docs/deploy_railway.md).
- The backend image is pinned to `python:3.12-slim-bookworm`: on Debian 13 (the current `-slim`), Playwright
  1.52's `install-deps` fails. Railway sets `INSTALL_PLAYWRIGHT=0` anyway (form-POST banner).
- The frontend Dockerfile moved to `frontend/Dockerfile`, with `runner` as the last stage, because Railway builds
  the final stage.

## Operations (live mode)
- **Two queues, two worker processes.** Award labeling took 1.5 h on the live site (4,168 record pages a day) and
  blocked the single worker, delaying the 2-hourly ingest. Long jobs (`@bulk`: labeling, backfill, retrain,
  initial load) now go to a `bulk` queue served by its own process. The scheduler skips a job that is already
  queued or running instead of piling up duplicates.
- **One DIBBS rate limit across processes.** With two worker processes the per-process 1 req/s limiter would
  allow 2 req/s, so the limiter takes a Redis `SET NX PX` slot per host when Redis is configured.
- **Award labeling only checks closed solicitations**, with a per-solicitation backoff (`award_checked_at`,
  migration 0003): every 2 days for the first 2 weeks after close, then 5, then 14, up to 75 days, at most 400
  record pages a run. Lines a broker bid on (or sent quote requests for) are checked first. Award listings by
  posting date cover the rest.
- **Nightly incremental backfill** (01:00 ET, live mode) grows history: PDFs for NSNs not yet covered (skipping
  NSNs whose history already came with a daily-ingest PDF), 3 older award posting dates, 10 NSN searches.
- **Data health** (`app/services/ops.py`) is public at `/api/health/data`: only DIBBS-derived counts and
  pipeline timestamps, nothing tenant-specific. Thresholds: ingest success within 14 h, labeling within 30 h,
  newest index file at most 4 days old (weekend + holiday).

## Recommendation quality (found on live data)
- **Minimum chance to win** (`org_settings.min_p_win`, default 20%, migration 0004). On live data 28% of
  bid-worthy lines were recommended at P(win) < 20% and 159 at the 5% grid edge, e.g. a lock washer whose history
  is $0.02-0.13 recommended at $10.29 (P(win) 5%, "expected profit" $77k). Those tail bids carried 47% of the
  summed expected profit. Wide (low-data) distributions push the argmax into the tail, which is also where the
  quantile model is least reliable, and a broker gains nothing from 1-in-20 bids. The bid search is now limited to
  P(win) >= min_p_win; if even the minimum-margin bid is below it, the line is a no-bid ("cost too high").
- **Stale predictions are re-priced.** 1,856 of 4,273 live open lines were priced as cold start, many of them for
  NSNs that do have history: it arrived after the line was first priced (ingest fetches at most 600 PDFs per run,
  backfill and labeling add rows later). Predictions now record `history_rows`; every ingest run re-prices open
  lines whose NSN has more award rows on file than when they were priced, and refreshes every org's
  recommendations for them. Only the ingest (default queue, never concurrent with itself) does this, so two jobs
  never upsert the same recommendations at once.
- **Quote first for cold, unquoted lines.** After the floor, the top of the live dashboard was still all
  cold-start NSNs (no award history) without supplier quotes, recommended at about 10x the predicted median: the
  price range there is FSC-wide and the provisional cost is a fraction of its median, so the "expected profit"
  is invented. Such lines now get no recommended bid (the price range is still shown) and a "Quote first" flag;
  they sort after priced lines (largest opportunities first), are counted apart in the dashboard summary and the
  digest, and get a normal recommendation as soon as a quote arrives. Lines with history but no quote keep a
  provisional recommendation. A rules change bumps `ENGINE_VERSION`, which re-computes every open line once
  (queued at boot, re-queued by the hourly ops check if a deploy kills it).
- **Provisional lines target the likely winning price** (engine v4). Without a supplier quote the landed cost is
  an assumption (70% of the predicted price), and optimizing expected profit against it still pushed bids to the
  win-probability floor whenever the range was wide (a 182 ft braid with one prior award at 1 ft: median $128,
  bid $276). Unquoted lines now bid at P(win) >= 50% (about the predicted median) and show the landed cost a
  supplier must beat for the minimum margin; quoted lines are optimized against the real cost with the org floor.
- **Confidence tiers and "best bets" ranking.** high: >= 3 prior awards and q90/q10 <= 2.2; medium: >= 2 awards and
  q90/q10 <= 4.5; low: the rest with history; none: no history (quote first). The default dashboard order is
  expected profit x weight (1 / 0.6 / 0.3), so the top of the list is the lines the model understands.
- **Engine refreshes are version-guarded**: the refresh job refuses to run (and to stamp the version) on a worker
  still running older code, and the worker queues it at startup, since a deploy overlap once let the outgoing
  worker stamp v3 on v2 rules.
- **Demo refresher** (`services/demo_refresh.py`, every 2 h): demo orgs only, up to 12 real open lines with
  simulated quotes from six fictional "(demo)" suppliers (placeholder CAGE codes ZZD01-06, example.com
  addresses), so a visitor sees the whole sourcing loop on live RFQs without any real company shown quoting.
- **Awarded lines are not opportunities.** DLA awards many automated (fast-award) solicitations before their
  return-by date. Lines with a posted award (award row for the solicitation + NSN) or a record-page status of
  Awarded/Cancelled now leave the dashboard, digest, demo, re-pricing and open-line counts
  (`services/biddable.py`). Before this, re-pricing folded a line's own winning price into its "prediction".
- **The live track record only counts predictions made before the award date** (and before the return-by date).
  The first version used the return-by date alone and showed a too-good-to-be-true 0.5% median error. It also
  starts on 2026-10-15 (`TRACK_RECORD_SINCE`): predictions before that came from the pre-fix pipeline (lines priced
  before their history arrived) and scored a 56% median error on 63 awarded lines; the track record measures the
  current system. History's "recommendation would win" likewise only counts recommendations last computed before
  the award posted.
- **Accuracy shown honestly.** On run 3's held-out window (830 real awards) the median absolute error of the
  model's median (17.3%) is no better than the inflation-adjusted last award (16.9%); the model's value is in
  calibrated ranges (80% / 90% coverage 76% / 89%) and the win-probability curve, and it wins on pinball loss.
  The landing page shows median error and range coverage, not a baseline comparison it doesn't win. Improving
  the point estimate is open work.
- **Dashboard selects columns**, extracting the three values it needs from the explanation JSON in SQL
  (`#>>` on Postgres, `json_extract` on SQLite) instead of loading thousands of full explanations. Responses are
  gzip-compressed (3.5 MB → about 320 KB for 4,300 lines).

## Pricing model v2 and data quality (2026-10-15; details in ml/research/README.md)
- **Derived award prices are quarantined unless verified.** Contract total / requested quantity (award
  listings, record pages) is wrong whenever DLA awarded another quantity or the total spans items. Training,
  evaluation, the live track record and live pricing all use only verified prices (RFQ-PDF history, award PDFs)
  plus derived prices within 5x of the NSN's verified median (`services/data_quality.py`). Award labeling
  fetches the award PDF first (budget 400 per run) and falls back to the derived price.
- **v2 adds part-number-family evidence for new parts** (`dibbs_ml/items.py`): other NSNs in the same FSC whose
  approved-source CAGE + part-number stem match. It is the cold-start reference when present, plus three
  features; name-only and manufacturer-only similarity were measured and dropped (no signal). Also a
  `niin_country` feature. v1 artifacts keep working (`cold_ref_mode` defaults to "fsc").
- **Model code changes trigger one retrain** (`MODEL_CODE_VERSION`, queued by the worker at startup and the
  hourly ops check; the job sets its own marker so an outgoing worker with older code can't). Promotion still
  requires a better pinball loss than the active model on the same held-out window.
- **Recent-data protocol and a deployed refit** (production research 2026-10-15): evaluate on train < T-4m,
  calibrate T-4m..T-3m, test the last 3 months (pinball 0.209 vs 0.212, 80% coverage 84% vs 78%); then deploy a
  refit on everything up to T-1m calibrated on the last month. The deployed artifact carries its held-out twin
  (`eval_model`), and later candidates are compared against that twin, never against a model that saw the test.
- **New parts with part-family evidence use the evidence as the median** (64% vs 88% median error), with the
  model's spread around it.
- **Accuracy is reported by history depth** (no history / 1 / 2-4 / 5+ awards) in the model metrics, the
  health endpoint and the landing page, because the overall median mixes two very different populations.

## Daily digest
- Weekdays 09:00 ET (after the 06:00/08:00 ingests), one email per owner/member with: the most profitable
  bid-worthy lines that arrived in the last 26 h, bid-worthy lines closing within 2 days without a recorded bid,
  and quotes waiting for review. No news, no email. Opt-out per user in Settings (`users.email_digest`). Never for
  demo/internal orgs, and skipped entirely while `EMAIL_PROVIDER=console` (it would only fill the outbox table).
  Idempotent per user per day (ingest_checkpoints `digest`).

## Accounts and the public demo
- **Read-only `viewer` role.** Enforced once, in `get_current_user`: any non-GET request from a viewer is 403,
  whatever the endpoint, so new endpoints can't forget the check. The UI wraps write controls in `WriteGuard`
  (a disabled `<fieldset>`). Owners can invite viewers too (e.g. an accountant).
- **Public demo = one-click viewer session** in the Atlas demo org (`POST /api/auth/demo`, `PUBLIC_DEMO=true`,
  rate-limited per IP). Sharing owner passwords would let any visitor change settings, invite users or send
  email. `DEMO_PASSWORD_LOGIN=false` replaces the published demo passwords with random ones at every boot
  (only while they still match the published value) and revokes those sessions.
- **`BOOTSTRAP_ADMIN_*`** instead of a shell command: Railway's shell needs an SSH key on the account, and the
  variable doubles as password rotation. Passwords under 12 characters are ignored.
- **Quote requests are drafted on demand.** Ingest used to create a draft per candidate supplier for every new
  line for every org (thousands a day, most with no email address to send to). Now the RFQ page drafts them
  (all candidates or one) and never sends without a click; the automatic path only runs for orgs with
  auto-send on, and only for candidates with an email address.
- **Landing, Terms and Privacy pages** describe what the product actually does (no bid submission, public data,
  per-org isolation, no tracking cookies). They are not legal advice and should be reviewed by counsel.

## Live QA pass (2026-10-15)
- **History outcomes**: "lost" only when the broker recorded a bid; an award to someone else without a recorded
  bid is "awarded" (counting those as losses made the win rate meaningless). "Recommendation would win" counts
  only recommendations computed before the award with a verified winning price (`data_quality` rule): derived
  listing prices produced "wins" like a $1.80 bid vs a $720 "winning price".
- **Nothing drawn from a guess**: on quote-first and no-reference lines the P(win) curve, the cost waterfall and
  the dashboard landed cost are hidden until a supplier quote exists; quote-first ranges wider than 20x are not
  shown at all.
- **Confidence needs agreeing history**: "high" also requires the last five inflation-adjusted awards to be within
  2.5x of each other ("medium": 6x). A narrow model range over scattered history (often a unit-of-issue change)
  was labeled high confidence.
- **Unusable quotes are ignored for cost**: expired quotes and quotes whose lead time exceeds the line's delivery
  days. Parsed quotes that need review still wait for approval; the RFQ page says so.
- **Demo data hygiene**: demo-org suppliers always carry "(demo)", simulated email threads get consistent
  timestamps, and `tidy_demo_org` repairs older demo rows idempotently (demo orgs only) on each demo refresh.
- **`SUPPORT_EMAIL`** is shown on the Terms and Privacy pages; until it is set they show no contact line.

## Defensible outputs (2026-10-15, engine v9)
Two independent reviews agreed the product's financial conclusions were stronger than the evidence behind them.
These changes make every shown number traceable to what was actually confirmed, and make decisions reconstructible.
- **No part-number fallback.** `store_extraction` used the part number *we asked for* when the supplier's reply did
  not name one, so "yes, $4.20 each" counted as a conforming approved-source quote. Only a part number the supplier
  stated can conform; a quote without one on an approved-source-only line is non-conforming with the reason
  "supplier did not state the part number". Manual entries (the broker typing a phoned quote) keep the requested part
  number, but the quote's evidence marks it `inferred`.
- **Quotes carry their pricing unit.** `price_unit` (what the supplier's price is per: EA, HD, M, BX, ...) and
  `price_basis_qty` (how many of the line's units of issue that price covers). `landed_cost.price_per_issue_unit`
  normalizes to the line's unit of issue: a stated basis divides, count units (EA/HD/M/DZ/GR/PR) convert, no stated
  unit is assumed per unit of issue (evidence `inferred`), and anything else (per box with no pack size) cannot be a
  cost: the quote needs review and cannot be approved until a basis is set. The mock LLM reads "per 100", "/C",
  "per M", "each", "for 25 pcs"; the Anthropic prompt asks for both fields.
- **Evidence states on quotes.** `supplier_quotes.evidence` = {part_number, unit, lead_time, validity, freight:
  confirmed | inferred | unknown}. Editing a field during review confirms it. The supplier panel shows "Assumed: ..."
  and "Not stated: ..." badges, and the recommendation shows the evidence behind the landed cost it uses.
- **No rank from an assumed cost.** Expected profit on an unquoted line is P(win) × (bid − 0.7 × median) × qty, so
  ranking by it ranked by order size. `rank_score` is now None unless the cost comes from a supplier quote; provisional
  lines carry `provisional_rank` (order size × confidence weight) and `expected_profit_basis = "assumed_cost"`, and
  the UI labels the figure "Profit if sourced ... if landed cost ≤ $X". The dashboard "best" sort puts quoted lines
  first and the rest by order size. (The triage score of the next step replaces the order-size fallback.)
- **Decision snapshots.** `recommendation_snapshots` (tenant-owned, append-only) records the recommendation whenever a
  decision-relevant value changes (bid, landed cost, P(win), no-bid, provisional, best quote, alternatives) and when
  the broker records an actual bid or actual cost: prediction id and quantiles, landed breakdown, quotes used, bids,
  P(win), expected profit and its basis, reasons, confidence, engine version. History judges "recommendation would
  win" on the last snapshot before the award date; lines from before snapshots existed fall back to `updated_at`.
  `GET /api/rfq/lines/{id}/snapshots` lists them ("Decision history" on the RFQ page).
- **Realized vs priced-for margin.** `recommendations.actual_cost_per_unit` (POST `/actual-cost`, shown on won lines)
  is the only basis for a realized margin; (bid − estimated landed) / bid is reported separately as the margin the bid
  was priced for.
- **Fair backtest.** The naive policies now face the same rules as the engine (no bid under cost + margin, none under
  the minimum P(win)), and every bid placed is charged a pursuit cost ($15 default), so the table compares pricing
  rules rather than a policy that protects itself against two that don't. `net_profit_per_line` charges the effort
  over every line looked at.

## Tenant cost model and triage score (2026-10-15, engine v10)
- **The provisional cost comes from the tenant's own quotes, not a flat fraction.** `dibbs_ml.cost_model.TenantCostModel`
  estimates the supplier price before anyone has quoted, from the first tier with enough observations: the tenant's
  quotes for this NSN (quantity-adjusted with the measured elasticity), then for other parts by the same approved-source
  manufacturer, then the same FSC and then all its quotes as a ratio to the predicted winning price, and finally the
  org's `provisional_cost_fraction` as a labelled assumption with a wide spread. Every estimate says its tier, count
  and basis; the landed-cost breakdown and the RFQ page repeat it. Nothing pools quotes across organizations: the
  product promises per-org isolation, and a pooled prior is a consent question, not a modelling one.
- **Leave-one-out is the evidence.** `TenantCostModel.leave_one_out()` scores each quote from the tenant's other
  quotes by tier; `ml/research/cost_lab.py` prints it per organization against the flat assumption. The local database
  only holds simulated demo quotes, so the numbers in `ml/research/README.md` show the method, not the market.
- **Triage score for unquoted lines** (`services/triage.py`): profit if sourced at the estimated cost × P(a supplier
  price lands under the target, from the cost estimate) × P(at least one candidate answers before the return-by date,
  from the tenant's own response rates and times, smoothed toward a 30% / 2-day prior) × P(winnable by a broker,
  `services/contestability.py`) − `pursuit_cost_per_line` (new org setting, default $15). The dashboard orders quoted
  lines by rank score, then unquoted lines by triage score, then by order size; the "Worth quoting" lane uses it too.
  The RFQ page shows every factor.
- **Contestability v1 is a rule** on the NSN's awards: a manufacturer (approved source or manufacturer guess) that won
  ≥ 80% of ≥ 3 awards leaves a reseller a 25% chance; a non-manufacturer incumbent at that share leaves 60%; otherwise
  100%. The competitor model of the next step replaces the rule behind the same interface.
- The candidate suppliers are computed once per line during the refresh and reused for the cost tier (which
  manufacturers), the response probability (who can be asked) and the drafts.

## Competitor model (2026-10-15, engine v11)
- **Contestability is the reseller share, not a rule.** `dibbs_ml.competitors.FscCompetitors` profiles every repeat
  winner per supply class from public awards (wins, parts, recency, price vs the inflation-adjusted previous award) and
  classifies CAGEs as manufacturers (approved source for any NSN, or DLA's manufacturer flag) or resellers. P(winnable)
  for a line = the part's reseller share of awards shrunk toward the supply class's share (three awards of prior),
  floored at 5%; the broker's own CAGE counts as a reseller. It replaces the v1 rule behind the same interface and
  carries the incumbents and the top supply-class players into the explanation.
- **Competitor view of P(win) is shown, not used.** prod_i (1 − p_i F_i(bid)) with incumbents bidding at their share
  of the part's awards and supply-class players at half theirs, each with a lognormal win-price ratio around the
  reference (shrunk to the class's pooled ratio, σ floor 0.15). Winning prices only are observed, so the view is biased
  low; the calibrated price model keeps the bid, and the RFQ page shows both with that caveat.
- The index is per supply class, built from verified prices (`data_quality`) and cached in-process for six hours
  (public data, the same for every tenant); the per-org part (own CAGE) is applied at query time.
- `ml/research/competitor_lab.py` scores the shrunk reseller share against the constant class share (Brier) and lists
  the top winners per class; the local synthetic run is in `ml/research/README.md`.

## DLA evaluation rules (2026-10-15, engine v12)
- **Source.** DLA's Master Solicitation for Automated Simplified Acquisitions, Part I "Automated Award Evaluation
  Information". dla.mil refuses non-browser downloads (HTTP 403), so the text was read from a PTAC presentation of
  revision 46 (2018) and cross-checked against search snippets of revision 104 (Feb 2026) and the DIBBS FAQ; every
  automated RFQ PDF links the current revision (105, May 2026). `services/evaluation.py` quotes the rules it encodes.
  A human should re-read Part I §3 of the current revision before trusting the details below in production.
- **Automated buys are price-only.** The program "evaluates all qualified quotations on the basis of price alone"
  after "price reasonableness logic and other automated filters"; past performance is not an automated factor. So the
  calibrated price model keeps P(win), and the bid search is capped at the band DLA has actually awarded within: the
  95th percentile of winning price / inflation-adjusted previous award on T/U solicitations in the supply class
  (`FscCompetitors.automated_ratio_p95`, needs 30 awards; falls back to all the class's awards, else no cap). The cap
  is data, not a guessed threshold; the explanation names it and whether it bound the bid, and a line whose
  minimum-margin bid is above it is a no-bid with that reason.
- **Qualified = bid without exception.** The chosen quote is checked for what would make the bid ineligible for
  automated award (alternate part number when approved parts exist; FOB origin terms on an FOB destination buy) and
  the evaluation factors DLA adds are listed (+$2,500 origin inspection on a destination buy, +$200 surplus).
- **Delivery is feasibility, not odds.** A supplier quote is usable only if lead time + the org's `handling_days`
  (new setting, default 3) fits the required delivery days; a longer delivery "is not an exception" but "can become an
  evaluation factor", so it is shown as a warning rather than modelled as a probability.
- **First article → manual; Q → manual.** The P(win) note says the price-only estimate is an approximation there.
- **Fast auto evaluation moves the deadline.** Evaluation starts 3 business days after issue and may award before the
  return date, so the triage's sourcing deadline is the earlier of the two.

## Enrichment behind coverage gates (2026-10-15)
- **PUB LOG characteristics are loaded from a file, format UNVERIFIED.** dla.mil refuses non-browser downloads, so the
  loader matches columns by name (NSN or NIIN+FSC, MRC, value, name) and the exact export layout is to be recorded in
  docs/data_sources.md §13 once a sample is in hand. Characteristics feed the similar-items index: same-name parts in a
  supply class agreeing on ≥ 2 characteristics are neighbours (reason "matching characteristics") and count as price
  evidence at their similarity weight. Their signal is unmeasured until data exists; part-number families remain the
  measured evidence.
- **Distributor price lists are per tenant and a cost tier, not an integration.** An imported price for the exact
  approved part number is the "catalog" tier (after the tenant's own NSN quotes, before ratio tiers; σ floor 0.10).
  No API integration was built: `coverage_lab.py` first measures what share of open lines a price list would match
  (electronics distributors such as Arrow would touch almost none of a fastener broker's lines). Catalog rows without
  a manufacturer CAGE match by part number alone.

## Portfolio, risk and sensitivity (2026-10-15, engine v13)
- **Fulfilment losses come off every won line.** `fulfilment_loss_pct` (org setting, default 2% of the bid) enters the
  engine: profit per unit is bid × (1 − loss) − landed and the minimum margin is net of it. Rejections, expediting
  and replacements are real costs the per-line EV ignored.
- **Sensitivity is part of the recommendation.** Every priced line is re-run through the same engine for three
  scenarios (supplier price +10%, competitors 5% lower, competitors 5% higher); `fragile` is set when a scenario flips
  bid ↔ no-bid or moves the bid by more than 15%, and the RFQ page opens the table when it is. A bid that a 10% supplier
  move overturns is shown as such rather than as a point optimum.
- **Portfolio limits hold lines back, they never drop them.** `services/portfolio.py` (`GET /api/portfolio`) sets the
  open book against the firm's limits: expected capital in flight (Σ P(win) × landed × qty, tied up for lead time +
  handling + payment terms) against `working_capital_limit`; expected awards (Σ P(win)) against `max_open_awards`;
  sourcing hours (pursue candidates × `pursuit_hours_per_line`) against `weekly_sourcing_hours`, pursuing the best
  triage scores first; and the share of expected capital on one supplier against `max_supplier_share`. Each limit
  returns the line ids it holds back and a note in plain words; the home page shows the book and the notes. Lines win
  independently in these sums: an assumption, stated in the module.
- Downside shown as such: the effort committed to lines being pursued (what is lost if none wins) and the chance that
  none of the quoted lines is won.

## Pilot loop (2026-10-15, step 7 of the maths plan)
Steps 1-6 made every number traceable; none of them proves the product makes a broker more money per hour than the
broker's own process. The pilot loop measures exactly that on a firm's real lines, and nothing in this plan counts as
proven until it does.
- **What is compared.** Awarded lines on which the tool's decision (bid or no-bid) sits in a `recommendation_snapshots`
  row dated BEFORE the award posted, the firm's own bid, the verified winning price and the landed cost: what the firm
  paid (`actual_cost_per_unit`; the cost import stores it landed = unit cost + freight / quantity) when recorded, else
  the estimate the recommendation was priced against, flagged `estimate` and excluded from the "real costs only" table.
  Per line: hindsight profit (a bid at the winning price), the firm's profit, the tool's counterfactual profit, each
  policy's regret (hindsight minus policy) and the increment (tool minus firm). `dibbs_ml.pilot` holds the maths.
- **Win rule and its bias.** Only the winning price is observed. A counterfactual bid wins iff it is below the winning
  price (ties lose) and, when the firm itself won, also at that price (every competitor was above it). A recommended bid
  above the firm's own winning bid is scored as a loss although the second-lowest bid is unknown; the bias runs against
  the tool and is listed in every report. Without a CAGE code on the organization the firm's outcome is inferred (a bid
  at or below the winning price won) and counted as such.
- **Hours.** Each bid placed costs `pursuit_hours_per_line` (org setting, step 6); a no-bid costs nothing; the firm's
  time on lines it skipped is unknown and not charged. The headline is the increment per hour the firm actually spent.
  "Completed profit" means the award posted and the outcome is known; fulfilment losses and payment timing are ignored.
- **Three comparisons, in order of fairness.** Head to head (lines the firm bid on where the tool had decided), the
  same on real costs only, and the tool as a policy over every line it decided (its bids on lines the firm skipped are
  counterfactual wins or losses). By supply class and by confidence tier for the head to head. The same evaluation adds
  the tenant cost model's leave-one-out error and the contestability Brier score (per supply class from public awards,
  and on the firm's own lines from the P(winnable) now stored in each snapshot).
- **Imports map onto existing rows.** `import-bids`, `import-costs`, `import-outcomes` (CLI; `pipelines/pilot_import.py`)
  read delimited files with columns matched by name. A bid or cost lands on the organization's recommendation for that
  line with an `actual_bid` / `actual_cost` snapshot dated when the firm acted. A line the tool never priced gets a
  `source = "historical"` recommendation (no bid, no price; the dashboard, digest and portfolio exclude it, History shows
  it as "not priced"); a solicitation the tool never ingested gets a stub solicitation + line in the public tables,
  closed on the bid date. Imports are idempotent (no duplicate snapshots).
- **Firm-reported outcomes are derived-grade data.** `import-outcomes` writes `award_history` rows with source
  `pilot_import` only where award labeling has none, requires the organization's CAGE (won lines are recognised by the
  awardee), and prices a won line at the firm's own bid when no price is given. The pilot evaluation trusts them for that
  tenant; everywhere else (training, competitors, History, the track record) they are treated like award-listing prices:
  kept only when they agree within 5x with the NSN's verified prices. They never enter `RELIABLE_SOURCES`.
- **Regret in model promotion.** `train_and_maybe_promote` now bids every awarded pilot line that falls in the
  candidate's held-out test window with both the candidate and the active model (their held-out twins, predicting from
  the frame rows of those awards, through the same engine with each organization's rules, against the real landed cost)
  and compares total regret (`metrics["pilot_regret"]`). **Threshold: 30 such lines** (`dibbs_ml.pilot.REGRET_MIN_LINES`);
  below it pinball loss alone decides, as before. Above it a candidate that beats the active model on pinball loss is
  still not promoted when its regret exceeds the active model's by more than 5% (`REGRET_TOLERANCE`): a better price
  distribution that bids worse on the lines the firm actually fought over is not an improvement for the firm. Thirty is
  where the sign of a profit difference stops flipping on one or two awards in the synthetic worlds; it is a starting
  point to revisit with the first pilot's data, and the report says "too early to call" below it.
- **Outputs.** `GET /api/history/pilot` and a "Pilot" section on the History page (verdict, profit per hour for both
  policies, increment, regret, by confidence tier, caveats); `pilot-report --org <id>` writes `ml/reports/pilot_<org>.md`
  with the same numbers, the per-line table and the caveats. The local database holds no pilot data, so no report is
  committed; the first real one replaces this sentence.

## Weapon-system catalog and the simple view (2026-11-18)
The product is pitched as a venture for a school project; fasteners undersold it. It now prices parts for five weapon
systems, and the screens lead with the answer while the maths stays one switch away.
- **Parts, not weapons.** DIBBS does not sell whole tanks, live grenades or RPGs to brokers (ammunition is Army-managed;
  the US does not buy RPGs). It buys their spare parts every day, so the catalog is five real supply classes: 2530
  (vehicular brake, steering, axle, wheel and **track** components: tank and combat-vehicle running gear), 1005 (guns
  through 30 mm: rifles and machine guns), 1010 (guns 30-75 mm: 40 mm grenade launchers), 1055 (rocket and pyrotechnic
  launchers) and 5855 (night vision). **2350 (tracked combat vehicles) was the first pick and was dropped**: the five
  captured index files (2026-10-04..08) hold no 2350 lines at all; tank track and wheels post under 2530. Live volume in
  those files: 1005 76 lines, 2530 107, 5855 13, 1010 7, 1055 2 (rocket launchers are thin on the live feed).
- **Defaults and demo data.** `DEFAULT_FSC_WATCHLIST` is the new five; the second demo org watches 1005 + 1010. The
  synthetic generator's `FSC_SPECS` hold weapon-part names, price ranges ($8 to $15k), part-number patterns and smaller
  typical quantities; synthetic NAICS follow the class (336992 tanks, 332994 small arms, 332995 ordnance, 333314 optics).
  The seed uses real history only when it covers the new classes. PPI: the offline CSV has no ordnance or optics series,
  so the broad metals index (WPU10) deflates all five, stated in `inflation.py`.
- **Demo line cap at the simplified acquisition threshold.** The $25,000 `max_line_value` default turned 10 of 40 demo lines
  into no-bids once a unit cost thousands; demo orgs (seed and the live switch) use $250,000. Real orgs keep the default.
  Approved-source-only parts without a conforming quote stay no-bids: that rule is the product's, not the catalog's.
- **Live switch, once, at boot.** `switch_demo_watchlists` moves demo orgs whose watchlist is still within the legacy
  fastener classes (`LEGACY_FSC_WATCHLIST`) to the new lists; a demo org on anything else, and every non-demo org, is left
  alone, so a paying firm never has its classes changed. Loading the new classes is **not queued from boot**: on the
  first deploy it was, and the outgoing worker (still listening during the deploy) took the jobs and lost them, leaving
  the live demo with no lines. Instead `queue_new_fsc_loads_if_needed` (live mode; worker startup after its 60 s delay,
  and the hourly ops check) queues the recent-RFQ load and `catalog_backfill_job` (800 PDFs, then a retrain under the
  normal promotion rules) for any watched class with no line on file and no `fsc_load` checkpoint; the load records
  the checkpoint, so a class with no recent RFQs is not retried. Fastener rows already in the database stay; they still
  train the model. The public top lines only show classes the demo org still watches.
- **Simple view by default, "Show the maths" in the header.** Off: the RFQ page shows the bid, chance to win, expected
  profit and cost, the win-chance curve, the price story, who wins the part, past prices and suppliers. On: the triage
  factors, cost-model tier, DLA evaluation checks, sensitivity, decision history, competitor model, similar parts, landed
  cost waterfall and price drivers; the lines table adds predicted median, landed cost and flags; the home page adds the
  portfolio ("Your book"). Nothing is computed differently; the switch is per browser (localStorage, falls back to the
  page view). The home page and landing page lead with the five categories.

## LightGBM quantile model stays as v1 (2026-11-18)

- **Kept as is, not simplified.** The project brief requires the model to beat a baseline. The live model (run 6,
  `lgbm_quantile_v2`) has a mean pinball loss of **0.2096** against **0.2674** for the last-award + FSC-residuals
  baseline on the same production data and time split (2026-10-15 run, `ml/research/README.md`): **22% lower**.
- **Why pinball is the right test.** The product prices off the whole winning-price distribution (the chance-to-win
  curve and its ranges), not one point guess, and pinball loss scores exactly that. The ranges are also honest: the 80%
  range held 84% of winning prices against 73% for the baseline.
- **Stated limit.** On parts with 5+ prior awards the median guess is no better than the baseline (13.0% vs 13.4%
  median error); the gain comes from calibrated ranges and from new parts (79% vs 92%).
- **The catalog is scored on its own.** The test window mixes in every class the history covers (mostly the old
  fastener classes), so every retrain also scores the five catalog classes alone, model vs baseline, overall and per
  class (`metrics[kind]["catalog"]`, `["by_fsc"]`), in the model report and in `/api/health/data` → `model.catalog`.
  Promotion still uses the whole window. The health check's top-level `pinball_mean` now comes from the same test window
  as its other numbers (it used to be the active run's original score), next to `baseline_pinball_mean`.
  `MODEL_CODE_VERSION` went to v2.3 so the next deploy retrains once and re-scores the live model this way.
