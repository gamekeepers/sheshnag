# Sheshnag — Setup Guide

Single source of truth for getting the platform running. Everything in this doc is audited against live code as of 2026-08-02.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Backend and daemon both need it |
| PostgreSQL | 14+ | Backend datastore — a reachable server and an account on it. You do **not** need to own or administer the server; see [Create the database](#0-create-the-database) |
| Node.js | 20+ (LTS) | Next.js 16 runtime; engine field is `>=18.18.0` |
| npm | 10+ | Ships with Node.js 20+ |
| Ollama **or** vLLM | any recent | Runtime for inference; Ollama is default for the daemon |

No sudo required. If you have no Postgres at all and want one locally, `docker run -d -e POSTGRES_USER=sheshnag -e POSTGRES_PASSWORD=sheshnag -e POSTGRES_DB=sheshnag -p 5432:5432 postgres:16` gives you one; pick a free host port if 5432 is taken. Production and rootless service setup are covered in [docs/services.md](services.md).

## Quick start (localhost)

Three terminals, three components. Copy `.env.example` first:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

### 0. Create the database

Do this once per environment, before the backend starts for the first time.
`Base.metadata.create_all()` creates the app's **tables**, but never the
database or the role — those must already exist or startup fails on connect.

Which path you take depends on what your Postgres account is allowed to do.
Find out first:

```bash
psql "postgresql://USER:PASSWORD@HOST:PORT/EXISTING_DB" \
  -tAc "select rolsuper, rolcreatedb from pg_roles where rolname = current_user;"
```

Two booleans come back, superuser and createdb — e.g. `f|t`.

> **Password in the URL:** URL-encode any of `@ : / ? # % &` in it —
> `p@ss` must be written `p%40ss` or the URL parses as a different host.

#### A. You can create databases (`rolcreatedb` = `t`)

The normal case, and the one to prefer — the app gets a database it owns.

```bash
createdb -h HOST -p PORT -U USER sheshnag
```

```ini
# backend/.env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/sheshnag
```

#### B. You cannot create databases (`rolcreatedb` = `f`)

Ask whoever administers the server for a database of your own — it keeps the
app's 15 tables isolated and makes backups and restores independent:

```sql
CREATE ROLE sheshnag LOGIN PASSWORD '<strong-password>';
CREATE DATABASE sheshnag OWNER sheshnag;
```

If that isn't available, a **dedicated schema inside a database you already
have** works without any elevated privilege — creating a schema needs only
`CREATE` on the database, which an ordinary application account usually has:

```bash
psql "postgresql://USER:PASSWORD@HOST:PORT/EXISTING_DB" \
  -c "CREATE SCHEMA IF NOT EXISTS sheshnag AUTHORIZATION USER;"
```

Then point the app at that schema through the connection URL. No code or
model changes are needed — `create_all()` follows `search_path`:

```ini
# backend/.env — note the URL-encoded '=' (%3D)
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/EXISTING_DB?options=-csearch_path%3Dsheshnag
```

Every table then lives in the `sheshnag` schema, invisible to anything using
that database's `public` schema.

#### Confirm before moving on

Connection problems are the most common first-run failure, and they are much
easier to read here than in a uvicorn traceback:

```bash
psql "postgresql://USER:PASSWORD@HOST:PORT/DATABASE" -c '\conninfo'
```

#### Resetting

There is no migration tool, so dropping and recreating is also how you pick
up a schema change. Match it to the path you used:

```bash
dropdb -h HOST -p PORT -U USER sheshnag && createdb -h HOST -p PORT -U USER sheshnag   # path A
psql "$DATABASE_URL" -c "DROP SCHEMA sheshnag CASCADE; CREATE SCHEMA sheshnag;"        # path B, schema
```

### 1. Backend (port 8000)

```bash
cd backend
pip install -r requirements.txt
# Edit backend/.env — at minimum set GOOGLE_CLIENT_ID for OAuth sign-in
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

On first startup `Base.metadata.create_all()` creates every table and the
model catalogue is seeded. A default superadmin is created too:
`admin@platform.com` / `admin`. You will be asked to change the password on
first login.

### 2. Frontend (port 3000)

```bash
npm install
# Edit .env — set NEXT_PUBLIC_BACKEND_URL and GOOGLE_CLIENT_ID if using OAuth
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 3. Daemon (on a GPU worker machine)

To try the daemon without a real backend or real GPUs, use mock mode — the
walkthrough lives in [`daemon/README.md`](../daemon/README.md#quick-start),
which is canonical for daemon-specific detail.

For real usage you need an org worker API key from the dashboard:

```bash
cd daemon
pip install -r requirements.txt
python -m daemon.main \
  --backend-url http://localhost:8000 \
  --api-key gk-your-org-worker-key
```

Or use the guided rootless installer: `scripts/install.sh` (no sudo; everything under `~/.gpu-daemon/`).

## Environment variable reference

Every variable the platform reads at runtime. Defaults are pulled from live code.

### Frontend (`app/`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | no | `http://localhost:8000` | Backend API base URL |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | if Google OAuth | _(must set)_ | Google OAuth client ID |
| `NEXT_PUBLIC_NGROK_ENABLED` | no | unset / falsy | Set `"true"` behind ngrok tunnels to skip browser warnings |

**Old `.env` caveat:** A stale variable `NEXT_PUBLIC_API_URL` used to exist. No code reads it — every call site uses `NEXT_PUBLIC_BACKEND_URL`. If your `.env` still has `API_URL=...`, the frontend silently falls back to port 8000. Remove the old entry or rename it.

### Backend (`backend/`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | yes (change in prod) | _(see below)_ | JWT signing key. The code ships a default for dev only; at startup a WARNING is logged if you haven't changed it. Generate one with: `openssl rand -hex 32`. |
| `DATABASE_URL` | yes | _(must set)_ | Postgres connection string. For local development, copy the example value from `backend/.env.example`; set it explicitly in every environment. |
| `GOOGLE_CLIENT_ID` | if Google OAuth | _(must set)_ | Must match the client ID registered with Google, and must also be the same value as `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. |
| `FRONTEND_URL` | no | `http://localhost:3000` | Base URL used in password-reset and invite email links. |
| `MAILGUN_API_KEY` | no | — | Mailgun API key. If unset, email sending is gracefully skipped. |
| `MAILGUN_DOMAIN` | no | — | Mailgun domain. Required alongside `MAILGUN_API_KEY` for emails to work. |
| `MAILGUN_FROM` | no | `Sheshnag support <noreply@sheshnag.io>` | Default sender address for platform emails. |
| `CORS_ORIGINS` | no | `"*"` (all origins) | Comma-separated list of allowed origins for CORS. Keep the default for local dev; set to your frontend URL(s) in production. See also the [credentials warning](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS/Errors/CORSMissingAllowCredentialsWildcard). |

**Old `.env` caveat:** `CORS_ORIGINS` used to be listed in `backend/.env.example` while doing nothing — origins were hardcoded to `["*"]` in `main.py`. It is now read from the environment, so the variable behaves as its name suggests and no code change is needed at deploy time.

### Daemon (`daemon/`)

Configured via a three-layer system: CLI > env (`DAEMON_*` prefix) > YAML file > defaults. Full precedence logic lives in `daemon/config.py`.

| Env var | Default | Description |
|---|---|---|
| `DAEMON_BACKEND_URL` | `http://localhost:8000` | Control plane API URL |
| `DAEMON_API_KEY` | _(required)_ | Org worker API key (created in dashboard) |
| `DAEMON_WORKER_ID` | auto-generated | Unique worker ID with hostname prefix |
| `DAEMON_RUNTIME` | `ollama` | Inference runtime: `ollama` or `vllm` |
| `DAEMON_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `DAEMON_VLLM_URL` | `http://localhost:8100` | vLLM server URL |
| `DAEMON_POLL_INTERVAL` | 5 | Seconds between job polls |
| `DAEMON_HEARTBEAT_INTERVAL` | 30 | Seconds between heartbeats |
| `DAEMON_INFERENCE_TIMEOUT` | 300.0 | Per-prompt inference timeout (seconds) |
| `DAEMON_LOG_LEVEL` | `INFO` | Log level: DEBUG / INFO / WARNING / ERROR |
| `DAEMON_WORK_DIR` | `~/.gpu-daemon/jobs` | Job artifacts directory |
| `DAEMON_MODELS` | _(empty)_ | Comma-separated list of model names |
| `DAEMON_GPU_NAME` | `unknown` | GPU model name for registration |
| `DAEMON_VRAM_GB` | 0.0 | GPU VRAM in GB for registration |

See `daemon/README.md` for full CLI flag and YAML config details.

## Production checklist

Before deploying to production, address each item and note whether it requires a code change or just configuration.

- [ ] **`SECRET_KEY`** — set a strong random value (e.g., `openssl rand -hex 32`). At startup the app logs a WARNING if the default is still in use.
- [ ] **Database** — provision the role and database as in [Create the database](#0-create-the-database), then point `DATABASE_URL` at it. The schema is created by `Base.metadata.create_all()` on first startup; there is no migration tool, so an existing database is never altered in place. Set up backups before the first real batch lands.
- [ ] **CORS origins** — set `CORS_ORIGINS` to your frontend URL(s) instead of `"*"`. Also review `allow_credentials=True` — browsers reject wildcard origins with credentials enabled, so you must list concrete origins when using cookies or auth headers.
- [ ] **HTTPS** — put a reverse proxy (Nginx, Caddy) in front of both frontend and backend. See [docs/services.md](services.md) admin appendix.
- [ ] **Google OAuth** — register your production domain with Google Cloud Console. Set the same `GOOGLE_CLIENT_ID` in both `.env` and `backend/.env`.
- [ ] **Email (Mailgun)** — configure `MAILGUN_*` vars for password reset, invites, and notifications. Gracefully skipped if unset, but you lose email functionality.
- [ ] **Reverse proxy** — route `/api/` to backend :8000, `/` to frontend :3000. See [docs/services.md](services.md) admin appendix.

---

## See also

- **Rootless services (systemd --user)** — [docs/services.md](services.md)
- **Google OAuth setup** — [docs/reference/google-oauth.md](reference/google-oauth.md)
- **Component-specific details** — [`backend/README.md`](../backend/README.md), [`daemon/README.md`](../daemon/README.md)
