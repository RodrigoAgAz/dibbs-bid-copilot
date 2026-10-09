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
