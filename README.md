# Sheshnag — Distributed Batch AI Compute Platform

Pools idle GPUs from researchers/labs and schedules asynchronous batch AI
inference jobs on them. Users submit OpenAI-style JSONL batches through a
dashboard/API; organizations host GPU workers by running a lightweight
daemon; the control plane validates, schedules, tracks, and returns
results.

It is **software you host**, not a service you sign up for.

## Start here

Pick whichever describes you. Each guide reads front to back, once.

| You want to… | Read | Roughly |
|---|---|---|
| **Submit jobs** to a deployment someone else runs | [docs/using-sheshnag.md](docs/using-sheshnag.md) | swap `base_url`, submit, poll, download |
| **Lend a GPU** to a deployment | [docs/provider.md](docs/provider.md) | one command, 10 minutes, no clone, no sudo |
| **Run Sheshnag** for your institution | [docs/self-host.md](docs/self-host.md) | an afternoon — Postgres, TLS, first provider |
| **Change the code** | [docs/develop.md](docs/develop.md) | three services locally, tests green |

Reference material — every endpoint, every environment variable, the data model,
the model catalogue — is under [`docs/reference/`](docs/reference/). The
[v1 spec](docs/spec/v1-spec.md) states intent and may lag the implementation.

The docs are a MkDocs site; each deployment serves its own copy at `/docs/`. To
read them locally, see [Documentation](#documentation) below.

## Components

| Component | Path | Stack | Docs |
|---|---|---|---|
| Control plane (API) | [`backend/`](backend/) | FastAPI + Postgres | [docs/reference/api.md](docs/reference/api.md) |
| Worker daemon | [`daemon/`](daemon/) | Python, Ollama/vLLM runtimes | [docs/reference/daemon.md](docs/reference/daemon.md) |
| Dashboard (this app) | [`app/`](app/) | Next.js | below |
| Spec & design docs | [`docs/`](docs/) | — | [docs/spec/v1-spec.md](docs/spec/v1-spec.md), [docs/reference/data-model.md](docs/reference/data-model.md) |

## Quick start

> The 60-second version, for someone who already knows the shape of it.
> If anything below is unclear, use [docs/develop.md](docs/develop.md) instead —
> it is the canonical local-setup guide and explains the database step, which
> this skips.

**Backend** (port 8000):

```bash
cd backend && pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**Frontend** (this Next.js app, port 3000):

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Default admin:
`admin@platform.com` / `admin` (forced password change on first login).

**Worker daemon** (on a GPU machine, needs an org worker API key from the
dashboard):

```bash
cd daemon && pip install -r requirements.txt
python -m daemon.main --backend-url http://localhost:8000 --api-key gk-...
```

Or the guided installer: `scripts/install.sh` (rootless — no sudo needed; installs to `~/.gpu-daemon` with `systemctl --user` services).

## Documentation

The docs are a [MkDocs](https://www.mkdocs.org/) site under [`docs/`](docs/),
routed by audience. Build or preview it locally:

```bash
python3 -m venv .venv-docs
.venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs serve          # http://127.0.0.1:8000
.venv-docs/bin/mkdocs build --strict # what CI runs
```

`--strict` fails on a broken internal link, so run it before opening a PR that
touches `docs/`. CI runs the same command on every such PR.

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE).
