# Sheshnag — Rootless Service Setup

Modelled after the repo's own `scripts/install.sh` pattern: everything under your home directory, managed with `systemctl --user`. No sudo required for daemon and backend. Frontend requires a one-time `next build`.

All three services are documented here so you can run Sheshnag hands-free on a worker machine or dev server.

## Prerequisites

Before running any of these:

1. **Clone the repo** (or have `scripts/install.sh` clone it for you):
   ```bash
   git clone https://github.com/gamekeepers/moonknight.git ~/.sheshnag
   ```
   The remote is still named `moonknight` — the repository name lags the
   product rename to Sheshnag. This is not a typo; clone it as shown.
2. **Environment files ready** — copy and edit from `.env.example`:
   ```bash
   cp .env.example .env.local              # frontend
   cp backend/.env.example backend/.env    # backend + daemon config
   ```
3. **Node modules installed** (frontend only):
   ```bash
   npm install
   ```

### systemd user session & linger

All three units use `systemctl --user`. To have them survive logout you need **linger** enabled:

```bash
loginctl enable-linger "$USER"
```

If that fails (common on managed distros), ask an admin to run:
```bash
sudo loginctl enable-linger "$(whoami)"
```

Without linger the services stop when your session ends. They still work fine for interactive development.

## 1. Daemon as user-level service

The daemon already has a unit template at `scripts/gpu-daemon.service`. Install it:

```bash
mkdir -p ~/.config/systemd/user/
cp scripts/gpu-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gpu-daemon
systemctl --user start gpu-daemon
```

**Status and logs:**
```bash
systemctl --user status gpu-daemon
journalctl --user -u gpu-daemon -f
```

### Configuration layers (highest → lowest)

1. CLI args passed in the unit's `ExecStart`
2. Environment variables (`DAEMON_*`) set in `~/.gpu-daemon/.env`
3. YAML config: `~/.gpu-daemon/config.yaml`
4. Defaults (see [setup.md](setup.md))

The unit reads `EnvironmentFile=-%h/.gpu-daemon/.env` so you can override any daemon setting without editing the unit.

### Ollama runtime as a user service

If the worker machine doesn't have Ollama installed, install it rootlessly via `scripts/install.sh` which places a user-local binary at `~/.gpu-daemon/bin/ollama`. The companion unit `scripts/ollama.service` runs it:

```bash
cp scripts/ollama.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ollama
```

The gpu-daemon unit lists `After=ollama.service` so Ollama starts first.

## 2. Backend as user-level service

The backend (FastAPI + uvicorn) needs a virtual environment and an env file with at least `SECRET_KEY`, `DATABASE_URL`, and `GOOGLE_CLIENT_ID`.

### Create the unit

```bash
mkdir -p ~/.config/systemd/user/
cp scripts/sheshnag-backend.service ~/.config/systemd/user/
```

Edit the unit to match your paths, then:

```bash
systemctl --user daemon-reload
systemctl --user enable sheshnag-backend
systemctl --user start sheshnag-backend
```

**Status and logs:**
```bash
systemctl --user status sheshnag-backend
journalctl --user -u sheshnag-backend -f
```

### Setup checklist for backend service

| Step | Command |
|---|---|
| Create venv in your home dir | `python3 -m venv ~/.sheshnag/venv` |
| Install deps | `~/.sheshnag/venv/bin/pip install -r backend/requirements.txt` |
| Copy & edit env file | Edit `~/.sheshnag/backend/.env` — set `SECRET_KEY`, `DATABASE_URL`, `GOOGLE_CLIENT_ID` |

The unit assumes:
- Repo cloned at `~/.sheshnag/`
- venv at `~/.sheshnag/venv/`
- Backend env file at `~/.sheshnag/backend/.env`

Adjust the paths in the unit if your layout differs.

### Production note: CORS

CORS origins are configured through `CORS_ORIGINS` in the backend env file — no
code change is needed. It defaults to `*`, which is fine on a loopback dev box
but should be narrowed before the service is reachable from anywhere else:

```bash
# in ~/.sheshnag/backend/.env
CORS_ORIGINS=https://sheshnag.example.com
```

Comma-separate multiple origins. Setting concrete origins also flips
`allow_credentials` on — browsers reject a wildcard origin when credentials are
allowed, so the backend derives the two together rather than letting them be set
independently. Restart the unit to pick up the change:

```bash
systemctl --user restart sheshnag-backend
```

See the [environment variable reference](setup.md#backend-backend) in the setup
guide for the full list.

## 3. Frontend as user-level service

Next.js in production runs `next build` once (ahead of time) then `next start`. The systemd unit wraps both steps: the `ExecStartPre` builds, then `ExecStart` serves on port 3005.

### Create the unit

```bash
mkdir -p ~/.config/systemd/user/
cp scripts/sheshnag-frontend.service ~/.config/systemd/user/
```

Edit the unit to match your paths, then:

```bash
systemctl --user daemon-reload
systemctl --user enable sheshnag-frontend
systemctl --user start sheshnag-frontend
```

**Status and logs:**
```bash
systemctl --user status sheshnag-frontend
journalctl --user -u sheshnag-frontend -f
```

### Setup checklist for frontend service

| Step | Command |
|---|---|
| Install node modules | `cd ~/.sheshnag && npm install` |
| Copy & edit env file | `cp .env.example ~/.sheshnag/.env.local` — set `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID` |

The unit assumes:
- Repo cloned at `~/.sheshnag/`
- Production env file at `~/.sheshnag/.env.local`

**Rebuild after env changes:** whenever you edit `.env.local`, restart the service to trigger a fresh build:
```bash
systemctl --user restart sheshnag-frontend
```

## Admin appendix (requires sudo)

Everything above runs without root. These items require an admin and are placed here for completeness.

### Nginx reverse proxy

To serve both frontend and backend from one domain on port 80/443:

```nginx
server {
    listen 80;
    server_name sheshnag.example.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8005/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /v1/ {
        # Direct backend API access (e.g. daemon calls)
        proxy_pass http://127.0.0.1:8005/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /workers/ {
        proxy_pass http://127.0.0.1:8005/workers/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://127.0.0.1:3005;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Adjust port mappings and paths for your deployment. The backend serves on `:8005`, frontend on `:3005`.

### HTTPS (certbot)

```bash
sudo certbot --nginx -d sheshnag.example.com
```

After obtaining the certificate, set `NEXT_PUBLIC_BACKEND_URL=https://sheshnag.example.com` in your frontend `.env.local` so the Next.js client calls the HTTPS endpoint instead of localhost.

### loginctl enable-linger for multiple users

If you're setting up services for a team (e.g., each worker user runs their own daemon), enable linger once per user:

```bash
sudo loginctl enable-linger worker-user
```

## See also

- **Setup guide** — [setup.md](setup.md) (prerequisites, env vars, quick start)
- **Daemon README** — [`daemon/README.md`](../daemon/README.md) (architecture, config details, API contract)
