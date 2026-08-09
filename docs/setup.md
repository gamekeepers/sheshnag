# Sheshnag — Setup Guide

Single source of truth for getting the platform running. Everything in this doc is audited against live code as of 2026-08-02.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Backend and daemon both need it |
| Node.js | 20+ (LTS) | Next.js 16 runtime; engine field is `>=18.18.0` |
| npm | 10+ | Ships with Node.js 20+ |
| Ollama **or** vLLM | any recent | Runtime for inference; Ollama is default for the daemon |

No sudo required for local development. Production and rootless service setup are covered in [docs/services.md](services.md).

## Quick start (localhost)

Three terminals, three components. Copy `.env.example` first:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

### 1. Backend (port 8005)

```bash
cd backend
pip install -r requirements.txt
# Edit backend/.env — at minimum set GOOGLE_CLIENT_ID for OAuth sign-in
python -m uvicorn main:app --host 127.0.0.1 --port 8005 --reload
```

A default superadmin is created on startup: `admin@platform.com` / `admin`. You will be asked to change the password on first login.

### 2. Frontend (port 3005)

```bash
npm install
# Edit .env — set NEXT_PUBLIC_BACKEND_URL and GOOGLE_CLIENT_ID if using OAuth
npm run dev
```

Open [http://localhost:3005](http://localhost:3005).

### 3. Daemon (on a GPU worker machine)

To try the daemon without a real backend or real GPUs, use mock mode — the
walkthrough lives in [`daemon/README.md`](../daemon/README.md#quick-start),
which is canonical for daemon-specific detail.

For real usage you need an org worker API key from the dashboard:

```bash
cd daemon
pip install -r requirements.txt
python -m daemon.main \
  --backend-url http://localhost:8005 \
  --api-key gk-your-org-worker-key
```

Or use the guided rootless installer: `scripts/install.sh` (no sudo; everything under `~/.gpu-daemon/`).

## Environment variable reference

Every variable the platform reads at runtime. Defaults are pulled from live code.

### Frontend (`app/`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | for production builds | `http://localhost:8005` (dev only) | Backend API base URL. `npm run dev` falls back to the default; `npm run build` **fails** if it is unset, because `NEXT_PUBLIC_*` values are inlined into the bundle at build time — an unset variable ships a production bundle pointing at the builder's own laptop, which surfaces later as a confusing CORS or network error. |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | if Google OAuth | _(must set)_ | Google OAuth client ID |
| `NEXT_PUBLIC_NGROK_ENABLED` | no | unset / falsy | Set `"true"` behind ngrok tunnels to skip browser warnings |

**Old `.env` caveat:** A stale variable `NEXT_PUBLIC_API_URL` used to exist. No code reads it — every call site uses `NEXT_PUBLIC_BACKEND_URL`. If your `.env` still has `API_URL=...`, the frontend silently falls back to port 8005. Remove the old entry or rename it.

### Backend (`backend/`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | yes (change in prod) | _(see below)_ | JWT signing key. The code ships a default for dev only; at startup a WARNING is logged if you haven't changed it. Generate one with: `openssl rand -hex 32`. |
| `DATABASE_URL` | no | `sqlite:///./jobs.db` | SQLAlchemy connection string. Swap for PostgreSQL in production. |
| `GOOGLE_CLIENT_ID` | if Google OAuth | _(must set)_ | Must match the client ID registered with Google, and must also be the same value as `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. |
| `FRONTEND_URL` | no | `http://localhost:3005` | Base URL used in password-reset and invite email links. |
| `MAILGUN_API_KEY` | no | — | Mailgun API key. If unset, email sending is gracefully skipped. |
| `MAILGUN_DOMAIN` | no | — | Mailgun domain. Required alongside `MAILGUN_API_KEY` for emails to work. |
| `MAILGUN_FROM` | no | `Sheshnag support <noreply@sheshnag.io>` | Default sender address for platform emails. |
| `CORS_ORIGINS` | no | `"*"` (all origins) | Comma-separated list of allowed origins for CORS. Keep the default for local dev; set to your frontend URL(s) in production. See also the [credentials warning](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS/Errors/CORSMissingAllowCredentialsWildcard). |

**Old `.env` caveat:** `CORS_ORIGINS` used to be listed in `backend/.env.example` while doing nothing — origins were hardcoded to `["*"]` in `main.py`. It is now read from the environment, so the variable behaves as its name suggests and no code change is needed at deploy time.

### Daemon (`daemon/`)

Configured via a three-layer system: CLI > env (`DAEMON_*` prefix) > YAML file > defaults. Full precedence logic lives in `daemon/config.py`.

| Env var | Default | Description |
|---|---|---|
| `DAEMON_BACKEND_URL` | `http://localhost:8005` | Control plane API URL |
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
- [ ] **Database backend** — swap `DATABASE_URL` from SQLite to PostgreSQL. Configurable via env; no code change needed.
- [ ] **CORS origins** — set `CORS_ORIGINS` to your frontend URL(s) instead of `"*"`. Also review `allow_credentials=True` — browsers reject wildcard origins with credentials enabled, so you must list concrete origins when using cookies or auth headers.
- [ ] **HTTPS** — put a reverse proxy (Nginx, Caddy) in front of both frontend and backend. See [docs/services.md](services.md) admin appendix.
- [ ] **Google OAuth** — register your production domain with Google Cloud Console. Set the same `GOOGLE_CLIENT_ID` in both `.env` and `backend/.env`.
- [ ] **Email (Mailgun)** — configure `MAILGUN_*` vars for password reset, invites, and notifications. Gracefully skipped if unset, but you lose email functionality.
- [ ] **Reverse proxy** — route `/api/` to backend :8005, `/` to frontend :3005. See [docs/services.md](services.md) admin appendix.

---

## See also

- **Rootless services (systemd --user)** — [docs/services.md](services.md)
- **Google OAuth setup** — [docs/google_oauth.md](google_oauth.md)
- **Component-specific details** — [`backend/README.md`](../backend/README.md), [`daemon/README.md`](../daemon/README.md)
