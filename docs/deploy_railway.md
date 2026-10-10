# Deploying to Railway

Six Railway services from one GitHub repo:

| Service | Root directory | Dockerfile path | Start command | Healthcheck | Public domain |
|---|---|---|---|---|---|
| **backend** | `/` | `infra/docker/backend.Dockerfile` | (image default: migrate + uvicorn) | `/api/health`, 600 s | not needed |
| **worker** | `/` | `infra/docker/backend.Dockerfile` | `python -m app.jobs.worker` | none | no |
| **scheduler** | `/` | `infra/docker/backend.Dockerfile` | `python -m app.jobs.scheduler` | none | no |
| **frontend** | `/frontend` | `Dockerfile` | (image default) | `/login` | **yes** |
| **Postgres** | Railway database | | | | no |
| **Redis** | Railway database | | | | no |

These are per-service settings (dashboard → service → Settings, or the GraphQL `serviceInstanceUpdate`
mutation). The repo deliberately has no `railway.json`: Railway has deprecated config-as-code, and its API
now rejects pointing services at config files.

The browser only ever talks to the frontend domain. Next.js proxies `/api/*` to the backend over Railway's
private network, so auth cookies stay first-party. Webhooks (inbound email, ElevenLabs, Twilio) also go through
the frontend domain: `https://<frontend-domain>/api/webhooks/...`.

## 1. Create the project

1. Railway → **New Project → Deploy from GitHub repo** → `RodrigoAgAz/dibbs-bid-copilot`. Rename the
   service to **`backend`** and set its settings from the table above.
2. **New → Database → PostgreSQL**, then **New → Database → Redis**.
3. Add the same GitHub repo three more times as **`worker`**, **`scheduler`** and **`frontend`**, each with the
   settings from the table.
4. **frontend** → Settings → Networking → **Generate Domain**.

With the CLI (`railway add -d postgres`, `railway add -s worker -r <owner>/<repo>`, `railway variable set`,
`railway domain -s frontend`) everything except the per-service settings can be scripted. Those settings
go through `railway api 'mutation { serviceInstanceUpdate(...) }'`.

The service names matter: the variables below reference `backend`, `frontend`, `Postgres` and `Redis`.
If yours differ, adjust the `${{...}}` references.

## 2. Variables

Generate secrets locally (one per value):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Shared variables** (Project Settings → Shared Variables), then share them with backend, worker and scheduler:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
JWT_SECRET=<generated secret>
INBOUND_EMAIL_SECRET=<generated secret>
FRONTEND_ORIGIN=https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}
PUBLIC_API_BASE_URL=https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}
COOKIE_SECURE=true
DEMO_MODE=true
CONNECTOR_MODE=fixture
INSTALL_PLAYWRIGHT=0
DIBBS_BANNER_MODE=form
```

**backend** only:

```
PORT=8000
AUTO_SEED=true
```

**frontend** only:

```
BACKEND_URL=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}
```

Notes:
- **Database URL:** `DATABASE_URL` from Railway is `postgresql://...`; the app switches it to the psycopg driver itself.
- **Playwright:** `INSTALL_PLAYWRIGHT=0` is a Docker build arg that skips Chromium (about 400 MB smaller images).
  The DoD banner is then accepted with a plain form POST (`DIBBS_BANNER_MODE=form`), which was verified against
  the live site.
- **`BACKEND_URL`** is baked into the frontend build. If you rename the backend service, redeploy the frontend.
- **`AUTO_SEED=true`** seeds the demo (orgs, synthetic history, 40 open RFQ lines, a trained model) on first
  boot, only when the database has no organizations. That takes about 1–2 minutes; the health check allows 10.

## 3. Deploy and log in

Deploy all services; Postgres and Redis come up first. Open `https://<frontend-domain>` and log in with
an account from `DEMO_CREDENTIALS.md`.

> **Change the demo passwords** (or don't use `AUTO_SEED`) if the URL is shared: they're in the repo.
> For a real deployment set `DEMO_MODE=false`, leave `AUTO_SEED` unset, sign up normally and create a
> superadmin with `python -m app.cli create-superadmin you@example.com` (Railway → backend → shell).

## 4. Going live with DIBBS data

On backend, worker and scheduler (shared variables):

```
CONNECTOR_MODE=live
SCRAPER_USER_AGENT=YourCompany-BidCopilot/1.0 (+mailto:you@yourcompany.com)
```

The scheduler then runs the real ingest at 06:00 ET. To build history, run a bounded backfill from the
backend shell; it's resumable, so run it repeatedly:

```bash
python -m app.cli backfill --mode live --start 2023-01-01 --end today --max-pdfs 500 --max-award-dates 2 --max-award-pages 5 --max-nsn-searches 10
python -m app.cli train
```

## 5. Optional integrations

Add the variables from the README ("Enabling live integrations") to the backend + worker:
- `ANTHROPIC_API_KEY`
- `EMAIL_PROVIDER=resend` with `RESEND_API_KEY`, `EMAIL_FROM` and `EMAIL_REPLY_DOMAIN`
- for voice, `VOICE_ENABLED=true` plus the Twilio and ElevenLabs keys

Point the inbound-email and voice webhooks at `https://<frontend-domain>/api/webhooks/...`.

## How it fits together

- **Backend start command:** `python -m app.cli bootstrap` (migrations, then the optional seed), then uvicorn
  on `::` (dual-stack, so the private network works) at `$PORT`.
- **Model artifacts** are stored in Postgres (`model_runs.artifact_blob`), so a model trained by the worker
  (Sunday 03:00 retrain) is immediately usable by the backend. There's no shared disk between services.
- **Files that don't persist:** the raw response cache (`data/raw`) and the report files (`ml/reports`) live on
  each container's ephemeral disk. They're regenerated as needed; the metrics the admin page shows are in the
  database.
