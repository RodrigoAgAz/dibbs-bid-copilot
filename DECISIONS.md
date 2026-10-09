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
