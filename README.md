# Sheshnag — Distributed Batch AI Compute Platform

Pools idle GPUs from researchers/labs and schedules asynchronous batch AI
inference jobs on them. Users submit OpenAI-style JSONL batches through a
dashboard/API; organizations host GPU workers by running a lightweight
daemon; the control plane validates, schedules, tracks, and returns
results.

## Components

| Component | Path | Stack | Docs |
|---|---|---|---|
| Control plane (API) | [`backend/`](backend/) | FastAPI + Postgres | [backend/README.md](backend/README.md) |
| Worker daemon | [`daemon/`](daemon/) | Python, Ollama/vLLM runtimes | [daemon/README.md](daemon/README.md) |
| Dashboard (this app) | [`app/`](app/) | Next.js | below |
| Spec & design docs | [`docs/`](docs/) | — | [docs/spec/v1-spec.md](docs/spec/v1-spec.md), [docs/reference/data-model.md](docs/reference/data-model.md) |

## Quick start

> Canonical setup guide: **[docs/setup.md](docs/setup.md)** — prerequisites,
> the full environment-variable reference, and the production checklist.
> Running as services (rootless `systemctl --user`): **[docs/services.md](docs/services.md)**.
> What follows is the 60-second version.

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
