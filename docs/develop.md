# Work on Sheshnag

**Who this is for:** you are changing the code. This gets all three services
running on your machine, the tests passing, and shows you how to drive a batch
end to end by hand.

*Verified against code: 2026-08-26.*

You need Postgres and Node, but you do **not** need a GPU — mock mode covers the
daemon, and most backend and frontend work never touches one.

---

## The three services


| Directory | Service | Stack |
|---|---|---|
| Root (`app/`, `package.json`) | Next.js frontend | JS (no TS), React 19, Tailwind v4 |
| `backend/` | FastAPI REST API + Postgres | Python, no Alembic |
| `daemon/` | GPU worker daemon | Python 3.12+, polls backend for jobs, runs Ollama (default) or vLLM |

Frontend reads `NEXT_PUBLIC_BACKEND_URL` (default `http://localhost:8000`).
Copy `.env.example` → `.env` for the frontend and `backend/.env.example` →
`backend/.env` for the backend.


## 1. Get it running locally

### Prerequisites


| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Backend and daemon both need it |
| PostgreSQL | 14+ | Backend datastore — a reachable server and an account on it. You do **not** need to own or administer the server; see [Create the database](self-host.md#1-create-the-database) |
| Node.js | 20+ (LTS) | Next.js 16 runtime; engine field is `>=18.18.0` |
| npm | 10+ | Ships with Node.js 20+ |
| Ollama **or** vLLM | any recent | Runtime for inference; Ollama is default for the daemon |

No sudo required. If you have no Postgres at all and want one locally, `docker run -d -e POSTGRES_USER=sheshnag -e POSTGRES_PASSWORD=sheshnag -e POSTGRES_DB=sheshnag -p 5432:5432 postgres:16` gives you one; pick a free host port if 5432 is taken. Production and rootless service setup are covered in [docs/self-host.md](self-host.md).

### The database

The backend creates its **tables** on first start but never the database or the
role. Create those first — the full set of paths, including what to do when your
Postgres account cannot create databases, is in
[Create the database](self-host.md#1-create-the-database).

#### 1. Backend (port 8000)

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

#### 2. Frontend (port 3000)

```bash
npm install
# Edit .env — set NEXT_PUBLIC_BACKEND_URL and GOOGLE_CLIENT_ID if using OAuth
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

#### 3. Daemon (on a GPU worker machine)

To try the daemon without a real backend or real GPUs, use mock mode —
[section 2](#2-without-a-gpu-or-a-backend) below. How the daemon is built, and
its contract with the backend, is in
[Daemon internals](reference/daemon.md).

For real usage you need an org worker API key from the dashboard:

```bash
cd daemon
pip install -r requirements.txt
python -m daemon.main \
  --backend-url http://localhost:8000 \
  --api-key gk-your-org-worker-key
```

Or use the guided rootless installer: `scripts/install.sh` (no sudo; everything under `~/.gpu-daemon/`).


## 2. Without a GPU or a backend

The daemon ships a mock control plane and a mock inference server, so you can
work on it with neither. Three terminals:

**Mock backend:**

```bash
cd daemon
pip install -r tests/requirements.txt
python -m tests.mock_backend
```

**A runtime** — either:

```bash
ollama serve                     # default runtime, port 11434
# or
vllm serve mistralai/Mistral-7B-Instruct-v0.2 --port 8100
```

**The daemon:**

```bash
cd daemon
python -m daemon.main --config config.yaml --api-key gk-anything-for-mock
```

The mock backend mirrors the real contract, so a daemon change that works here
usually works against the real control plane. It does not validate batches or
persist anything.

## 3. Running the tests


| Area | Command |
|---|---|
| Backend | `cd backend && python -m pytest tests/ -q` — needs a Postgres at `TEST_DATABASE_URL` (see `tests/conftest.py`) |
| Daemon | `cd daemon && python -m pytest tests/ -q` — needs `pip install -e ".[dev]"` |
| Frontend | `npm run lint` — no test framework yet |

!!! danger "`TEST_DATABASE_URL` must name a database you do not care about"
    The backend suite **drops every table** in that database at session start
    and again at the end. Never point it at `DATABASE_URL`, and never at a
    database holding anything you want to keep. A dedicated database, or a
    dedicated schema via
    `?options=-csearch_path%3Dsheshnag_test`, is the safe shape —
    `backend/tests/conftest.py` documents it and reports exactly what it would
    destroy.

Two more traps:

- **`pytest-asyncio` is in the `[dev]` extra, not `daemon/requirements.txt`.**
  Without it the async daemon tests fail to run rather than passing — and those
  are the ones covering the interesting logic.
- **`backend/.gitignore` ignores `test_*.py`.** New backend tests need
  `git add -f` or they silently never get committed.


## 4. Drive the whole loop by hand

Worth doing once. It verifies worker registration and the poll/execute/upload
loop **without the frontend**, using Swagger (`/docs` on the backend), a shell,
and the daemon.

!!! note "This runbook was rewritten on 2026-08-26"
    An earlier version worked around a real onboarding gap: no API could create
    an organisation's *first* worker key, so it seeded one with a script poking
    the ORM directly. **That gap is closed** —
    `POST /v1/orgs/{org_id}/api-keys` (`backend/routers/organizations.py:141`)
    creates keys, and the Provider portal calls it. The workaround is gone; step
    3 is now an ordinary API call. It also predated Postgres and still said
    `rm -f jobs.db`.

### 1. Start a backend on an empty database

Drop and recreate as in [Resetting](self-host.md#resetting), then start it.
Startup seeds the model catalogue, a default superadmin
(`admin@platform.com` / `admin`) and a "Platform Admin Org" that owns it.
`must_change_password` is informational here — the JWT works immediately.

Swagger UI: <http://localhost:8000/docs>.

### 2. Log in and find the org id

1. `POST /v1/auth/login` with `{"email": "admin@platform.com", "password": "admin"}`
   → copy `access_token`, click **Authorize**, paste it.
2. `GET /v1/orgs` → note the Platform Admin Org's `id`.

### 3. Create a worker key

`POST /v1/orgs/{org_id}/api-keys` with a name. The raw `gk-…` key is in the
response **once** — copy it now.

### 4. Register by running the daemon

```bash
cd daemon
python -m daemon.main \
  --backend-url http://localhost:8000 \
  --api-key gk-<the-key> \
  --models mistral:7b
```

The registration flow *is* the verification. Watch for the startup banner
reporting auth configured, hardware detection, then **`Worker registered:
worker-…`** with a backend-assigned id, then heartbeat lines every ~30s.

Three things that look like failures but are not:

- **No GPU on the box** — it registers with an empty GPU list and heartbeats
  zero VRAM. Correct.
- **Ollama not running** — registration and heartbeats work fine without it. It
  is only needed to *execute* a batch.
- `cat ~/.gpu-daemon/credentials` shows the key and the assigned `worker_id`,
  mode `600`. That file is how the daemon survives a backend outage.

### 5. Verify from Swagger

Re-**Authorize** with the JWT first: `/v1/*` endpoints reject worker keys and
`/workers/*` rejects JWTs.

`GET /v1/orgs/{org_id}/workers` → your hostname, `status: online`,
`activity: idle`, the runtime you configured, and heartbeat-fed VRAM and loaded
models.

### 6. Behaviour worth checking

- **Re-registration:** restart the daemon → still **one** row with the **same**
  `worker_id`. Hostname plus org match means update, not duplicate.
- **Registration-failure fallback:** stop the backend, start the daemon → it
  logs the failure and continues as the saved `worker_id`.
- **Offline sweep:** kill the daemon and wait. `HEARTBEAT_TIMEOUT_SECONDS = 120`
  and `SWEEP_INTERVAL_SECONDS = 60` (`backend/sweeper.py`), so within about three
  minutes the worker flips to `offline` with `activity` frozen at its last value.
- **Wrong key:** a bogus `gk-` key gets a 401 at registration and the daemon
  exits.

### 7. Push a batch through it

This is the only part that needs Ollama running and a **catalogued** model.

1. **The model must be in the catalogue and the worker must host its runtime
   id.** `body.model` is a catalogue slug from `GET /v1/models` — e.g.
   `mistral-7b-instruct-q4-ollama`, whose `runtime_model_id` is `mistral:7b`.
   The worker must have that pulled (`ollama pull mistral:7b`) and run with
   `--runtime ollama`. A batch for a model no worker hosts is never assigned; a
   `body.model` that is not in the catalogue fails validation with
   `unsupported_model`.
2. **Upload the input:** `POST /v1/files`, multipart, `purpose="batch"` → note
   the `file-…` id. One line looks like:
   ```json
   {"custom_id":"c1","method":"POST","url":"/v1/chat/completions","body":{"model":"mistral-7b-instruct-q4-ollama","messages":[{"role":"user","content":"hi"}],"max_tokens":64}}
   ```
3. **Submit:** `POST /v1/batches` with
   `{"input_file_id":"file-…","endpoint":"/v1/chat/completions","completion_window":"24h"}`.
   Watch `validating → validated` via `GET /v1/batches/{id}` or the `/events`
   SSE stream.
4. The daemon's next poll claims it → `in_progress` → prompts run →
   `POST /workers/upload-results` → `completed`.
5. **Download:** `GET /v1/batches/{id}` for the `output_file_id`, then
   `GET /v1/files/{output_file_id}/content`.

## Quirks that will bite you

### Frontend

- **No TypeScript** — JS throughout (see `jsconfig.json`). Path alias `@/*`
  maps to repo root (`./*`), not `app/`.
- Tailwind v4 via `@tailwindcss/postcss` (no `tailwind.config`). CSS lives in
  `app/globals.css`.

### Backend

- **Postgres via `DATABASE_URL`**, schema auto-created via
  `Base.metadata.create_all()`. No Alembic. To reset, drop and recreate the
  database and restart.
- **`create_all()` never adds columns to an existing table.** A new column on
  an existing model appears on fresh databases only — everyone else needs to
  recreate theirs, or add the column by hand.
- **Do work at call time, not import time.** pytest imports every module during
  collection, so a module-scope `raise` or config read takes out the whole
  suite rather than one test. This has happened.
- Default admin on first startup: `admin@platform.com` / `admin` (forced
  password change).
- Auth: JWT for dashboard users, `gk-*` API keys for workers/programmatic
  access. Router prefixes are `/v1/*` and `/workers/*`.

### Daemon

- Config precedence: CLI args > env (`DAEMON_*`) > `config.yaml` > defaults.
  The mapping is `_ENV_MAP` in `daemon/daemon/config.py`.
- Executors use the Strategy pattern — subclass
  `daemon/daemon/executors/base.py` to add a runtime without touching
  `worker.py`. Dependencies are constructor-injected specifically so tests can
  substitute them; keep it that way.
- `OllamaExecutor._translate_request` rebuilds the request from a **hard
  whitelist**. Any OpenAI parameter not named there is dropped silently — be
  deliberate when adding to it.

## Conventions

- **One canonical home per fact.** If something is already documented, link to
  it rather than restating it. Component READMEs point at these guides, not the
  other way round.
- **Doc links:** relative with the `.md` extension inside `docs/`; a
  `https://github.com/gamekeepers/sheshnag/blob/develop/…` URL for code or
  anything outside `docs/`. Relative paths cannot resolve outside `docs_dir`, so
  the site build fails on them.
- **Run `mkdocs build --strict`** before opening a PR that touches `docs/`. CI
  runs it too. It fails on broken links and anchors.
- The process rules — branching, staging, never pushing or merging on someone's
  behalf — are in
  [`CONTRIBUTING.md`](https://github.com/gamekeepers/sheshnag/blob/develop/CONTRIBUTING.md)
  and, for agents,
  [`AGENTS.md`](https://github.com/gamekeepers/sheshnag/blob/develop/AGENTS.md).

## See also

- [Model catalogue](reference/model-catalogue.md) — every servable model is a pinned catalogue entry; there is deliberately no path to run an uncatalogued one
- [Data model](reference/data-model.md) — the tables and why they are shaped that way
- [OpenAI compatibility](reference/openai-compatibility.md) — which parameters are honoured, ignored or rejected
- [Structured outputs](reference/structured-outputs.md)
- [Machine inspection](reference/machine-inspection.md) — what the daemon detects about a host
- [Run it for your institution](self-host.md) — the production path
- [Lend your GPU](provider.md) — the provider path
