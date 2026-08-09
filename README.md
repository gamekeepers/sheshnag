# Sheshnag — Distributed Batch AI Compute Platform

Pools idle GPUs from researchers/labs and schedules asynchronous batch AI
inference jobs on them. Users submit OpenAI-style JSONL batches through a
dashboard/API; organizations host GPU workers by running a lightweight
daemon; the control plane validates, schedules, tracks, and returns
results.

## Components

| Component | Path | Stack | Docs |
|---|---|---|---|
| Control plane (API) | [`backend/`](backend/) | FastAPI + SQLite | [backend/README.md](backend/README.md) |
| Worker daemon | [`daemon/`](daemon/) | Python, Ollama/vLLM runtimes | [daemon/README.md](daemon/README.md) |
| Dashboard (this app) | [`app/`](app/) | Next.js | below |
| Spec & design docs | [`docs/`](docs/) | — | [docs/v1-spec.md](docs/v1-spec.md), [docs/revised_db_schema.md](docs/revised_db_schema.md) |

## Quick start

> Canonical setup guide: **[docs/setup.md](docs/setup.md)** — prerequisites,
> the full environment-variable reference, and the production checklist.
> Running as services (rootless `systemctl --user`): **[docs/services.md](docs/services.md)**.
> What follows is the 60-second version.

**Backend** (port 8005):

```bash
cd backend && pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8005 --reload
```

**Frontend** (this Next.js app, port 3005):

```bash
npm install
npm run dev
```

Open [http://localhost:3005](http://localhost:3005). Default admin:
`admin@platform.com` / `admin` (forced password change on first login).

**Worker daemon** (on a GPU machine, needs an org worker API key from the
dashboard):

```bash
cd daemon && pip install -r requirements.txt
python -m daemon.main --backend-url http://localhost:8005 --api-key gk-...
```

Or the guided installer: `scripts/install.sh` (rootless — no sudo needed; installs to `~/.gpu-daemon` with `systemctl --user` services).
