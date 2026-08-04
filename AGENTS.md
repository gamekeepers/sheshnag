# Sheshnag — Batch AI Compute Platform

Instructions for any coding agent working in this repo (Claude Code, Codex,
Cursor, …). Humans: the same process rules apply — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

> The repository is still named `moonknight`; the product was renamed to
> **Sheshnag**. Both names appear in older docs. Use Sheshnag in anything new.

---

## Rules for agents — read before touching anything

These are hard constraints, not preferences.

1. **Never push.** No `git push`, to any branch, ever. A human pushes.
2. **Never merge.** No `gh pr merge`, no `git merge` into `main`, no
   "the PR looked approved so I merged it".
3. **Never open a PR.** Prepare the branch and the description; a human opens it.
4. **Never work on `main`.** Create `task/<slug>` before editing a single file.
   If the working tree is already dirty, branch from HEAD and leave those
   changes alone.
5. **Never rewrite shared history** — no force-push, no rebase of a pushed
   branch, no `git reset --hard` on a branch that exists on the remote.
6. **Stage only what you changed.** `git add <paths>`, never `git add -A`.
   This repo routinely has unrelated untracked files in the tree.
7. **Verify before you report done.** Run the relevant suite (below) and say
   what you ran and what it printed. "Should work" is not a result.

`.claude/settings.json` denies the push/merge/rebase commands for Claude Code.
Treat that as a backstop for mistakes, not as the boundary of what is allowed —
the rules above bind you regardless of which tool you are.

**If a task seems to require pushing or merging, stop and say so.** That is a
signal the task needs a human, not a signal to find a way around the rule.

---

## Architecture (3-service monorepo)

| Directory | Service | Stack |
|---|---|---|
| Root (`app/`, `package.json`) | Next.js frontend | JS (no TS), React 19, Tailwind v4 |
| `backend/` | FastAPI REST API + SQLite | Python, no Alembic |
| `daemon/` | GPU worker daemon | Python 3.12+, polls backend for jobs, runs Ollama (default) or vLLM |

Frontend reads `NEXT_PUBLIC_BACKEND_URL` (default `http://localhost:8000`).
Copy `.env.example` → `.env` for the frontend and `backend/.env.example` →
`backend/.env` for the backend.

Canonical setup guide: [`docs/setup.md`](docs/setup.md). Running as rootless
user services: [`docs/services.md`](docs/services.md).

## Developer commands

**Frontend:**
```bash
npm run dev           # http://localhost:3000
npm run build
npm run lint          # eslint (next/core-web-vitals)
```

**Backend:**
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
# Swagger UI at http://localhost:8000/docs
```

**Daemon:**
```bash
cd daemon
pip install -e ".[dev]"       # editable + test deps
gpu-daemon --config config.yaml
```

To exercise the daemon without a real backend, run `python -m tests.mock_backend`
in a separate terminal first.

## Testing

| Area | Command |
|---|---|
| Backend | `cd backend && python -m pytest tests/ -q` — 42 tests, in-memory SQLite |
| Daemon | `cd daemon && python -m pytest tests/ -q` — needs `pip install -e ".[dev]"` |
| Frontend | `npm run lint` — no test framework yet |

Two traps:

- **`pytest-asyncio` is in the `[dev]` extra, not `daemon/requirements.txt`.**
  Without it the async daemon tests fail to run rather than passing — and those
  are the ones covering the interesting logic.
- **`backend/.gitignore` ignores `test_*.py`.** New backend tests need
  `git add -f` or they silently never get committed.

## Frontend quirks

- **No TypeScript** — JS throughout (see `jsconfig.json`). Path alias `@/*`
  maps to repo root (`./*`), not `app/`.
- Tailwind v4 via `@tailwindcss/postcss` (no `tailwind.config`). CSS lives in
  `app/globals.css`.

## Backend quirks

- **SQLite at `backend/jobs.db`**, auto-created via
  `Base.metadata.create_all()`. No Alembic. To reset, delete `jobs.db` and
  restart.
- **`create_all()` never adds columns to an existing table.** New columns on an
  existing model must also go in `_NEW_COLUMNS` in `backend/migrations.py`, or
  they silently won't exist on anyone's current DB.
- **Do work at call time, not import time.** pytest imports every module during
  collection, so a module-scope `raise` or config read takes out the whole
  suite rather than one test. This has happened.
- Default admin on first startup: `admin@platform.com` / `admin` (forced
  password change).
- Auth: JWT for dashboard users, `gk-*` API keys for workers/programmatic
  access. Router prefixes are `/v1/*` and `/workers/*`.

## Daemon quirks

- Config precedence: CLI args > env (`DAEMON_*`) > `config.yaml` > defaults.
  The mapping is `_ENV_MAP` in `daemon/daemon/config.py`.
- Executors use the Strategy pattern — subclass
  `daemon/daemon/executors/base.py` to add a runtime without touching
  `worker.py`. Dependencies are constructor-injected specifically so tests can
  substitute them; keep it that way.
- `OllamaExecutor._translate_request` rebuilds the request from a **hard
  whitelist**. Any OpenAI parameter not named there is dropped silently — be
  deliberate when adding to it.

## Models

Every servable model is a **pinned catalogue entry** (weights + quantization +
runtime), matched to workers by digest. Identity is curated; availability is
derived from worker registrations. There is deliberately **no path to run an
uncatalogued model**. See [`docs/model_catalogue.md`](docs/model_catalogue.md)
before changing anything in this area.
