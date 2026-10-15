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
