# CLAUDE.md: DIBBS Bid Copilot

Multi-tenant SaaS that helps small federal contractors price DLA DIBBS solicitations (RFQs).
It ingests RFQs, enriches them with NSN procurement history, predicts the distribution of the
winning unit price, collects supplier quotes (email; optional AI voice), and recommends a bid.
**The app never submits bids to DIBBS.**

## Layout

```
backend/            FastAPI app (Python 3.12, SQLAlchemy 2, Alembic, Pydantic v2)
  app/
    main.py         app factory, CORS, CSRF middleware, router registration
    config.py       all settings (env vars, see .env.example)
    db.py           engine/session; SQLite allowed only for local dev/tests
    models/         ORM: tenancy.py, dibbs.py (public data), sourcing.py, pricing.py, types.py
    core/           security.py (argon2, JWT, cookies, CSRF), tenancy.py, ratelimit.py
    api/deps.py     get_current_user, get_tenant (THE tenancy dependency), role guards
    api/routes/     one router per area (auth, org+settings, suppliers, dashboard, rfq, quotes, history, admin, webhooks)
    connectors/     one module per external source (fixture + live modes, rate limiting, raw cache)
    pipelines/      backfill, daily ingest, award labeling, FSC volume report
    services/       pricing (model inference), recommendations, landed cost, cost_model (tenant cost tiers), triage,
                    contestability + competitors, evaluation (DLA rules), sensitivity, portfolio, sourcing, email,
                    llm, voice, training
    pipelines/      also enrichment.py: FLIS/PUB LOG characteristics and distributor catalog imports
    jobs/           RQ queue/worker, APScheduler schedules, task functions
    seed/           synthetic history + demo orgs (make seed)
    cli.py          `python -m app.cli <cmd>`: migrate, seed, backfill, ingest, labels, train, fsc-report, ...
  alembic/          migrations (portable types: run on Postgres and SQLite)
  tests/            pytest; fixtures/ holds REAL captured DIBBS samples
ml/
  dibbs_ml/         features (leakage-safe), baseline, LightGBM quantiles, conformal, CDF, bidding engine,
                    cost_model (tenant cost tiers), competitors (profiles, reseller-share contestability),
                    evaluation, backtest, reports. Pure Python, no DB models (reads via SQL / DataFrames).
  data/             static PPI CSV (+ scripts/refresh_ppi.py)
  reports/          generated: latest_model.md, fsc_volume.md, PNG charts
  tests/            ML unit tests (incl. the leakage test and recommendation-math tests)
frontend/           Next.js 15 App Router + TS + Tailwind + shadcn-style UI + TanStack Query + Recharts
  src/lib/api/      openapi-fetch client + generated schema.d.ts (npm run gen:api)
  e2e/              Playwright end-to-end test
infra/              Dockerfiles, postgres init
docs/               data_sources.md (observed DIBBS formats)
```

## Commands

Docker (target environment): `docker compose up -d && make seed`, then `make test`, `make lint`,
`make ingest`, `make backfill FSC=5305,5310 START=2023-01-01 END=today`, `make train`.

Without Docker/make (this repo was built on Windows without either): `python dev.py <target>`
mirrors the Makefile and starts an embedded PostgreSQL 16 (`pgserver`) automatically:
`python dev.py setup`, `python dev.py seed`, `python dev.py api`, `python dev.py web`, `python dev.py test`.

Backend tests: `cd backend && .venv/Scripts/python -m pytest` (uses TEST_DATABASE_URL, else embedded
Postgres, else SQLite). Lint: `ruff check app tests ../ml`. Frontend: `npm run typecheck`, `npm run lint`,
`npm run test:unit`, `npx playwright test`.

After changing API schemas: `python dev.py api-types` (or `make api-types`) to regenerate
`frontend/src/lib/api/schema.d.ts`.

## Conventions

- **Tenancy**: tenant-owned models inherit `TenantOwned` and have `organization_id`. Endpoints get a
  `TenantContext` from `Depends(get_tenant)`, which binds the session to the org; SQLAlchemy hooks
  in `core/tenancy.py` then filter every ORM query and reject cross-org writes. Never bypass this in
  request handlers (the only escape hatch, `SKIP_TENANT_FILTER`, is for read-only global uniqueness checks).
  Public DIBBS data (solicitations, lines, awards, companies, predictions) is global.
- **Auth**: JWT access token (15 min) + rotating opaque refresh token (30 days), httpOnly cookies;
  mutating cookie-authenticated requests need the `x-csrf-token` header = `csrf_token` cookie.
  Roles: owner / member / viewer. Viewers (and the public demo session) get 403 on every non-GET request in
  `get_current_user`; in the UI wrap write controls in `WriteGuard` (`components/common.tsx`).
- **Jobs**: `app/jobs/queue.py` has two RQ queues; mark long jobs `@bulk` so they run on the bulk worker
  process, never blocking ingest. The scheduler uses `enqueue_unique`. DIBBS requests share one 1 req/s
  limit across processes via Redis.
- **External calls** always have a deterministic offline twin: fixture connectors, MockLLM,
  ConsoleEmailProvider, MockVoiceProvider. Tests and demo mode never touch the network.
- **Connectors**: max 1 req/s per host, exponential backoff, `SCRAPER_USER_AGENT`, raw responses
  cached in `data/raw/<source>/<date>/`, idempotent upserts. Public pages only.
- **ML**: never random splits; features use strictly-prior data only (see `ml/tests/test_leakage.py`).
  Training and inference share `dibbs_ml.features.compute_features` (no train/serve skew).
- Money is USD floats rounded to cents at the edges; prices are per unit of issue.
- Record non-obvious choices in `DECISIONS.md`.

## Frontend notes

- Pages: `src/app/(public)/{page,terms,privacy}` (landing at `/` for signed-out visitors), `src/app/(auth)/{login,signup,forgot-password,reset-password}`, `src/app/(app)/{dashboard,rfq/[solicitationNumber],history,suppliers,settings,admin}`.
- All data access goes through `api` (openapi-fetch + generated `schema.d.ts`) and TanStack Query; invalidate
  `["rfq", sn]` / `["dashboard"]` after mutations. Pure logic lives in `src/lib/{format,charts,dashboard}.ts`
  with Vitest tests.
- Recharts animations are disabled (`isAnimationActive={false}`): they stall in background tabs and make
  screenshots/e2e flaky.
- `src/middleware.ts` gates app routes on the `csrf_token` cookie; the API is the real authority.

## Demo data

`make seed` resets demo orgs (Atlas/Bluewater), synthetic `SPEZZ*` solicitations and `source='synthetic'`
history, then trains. Synthetic data never collides with real DIBBS numbers, and real data
(`CONNECTOR_MODE=fixture|live` ingest/backfill) can coexist in the same database.
