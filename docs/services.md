# Sheshnag — Rootless Service Setup

Modelled after the repo's own `scripts/install.sh` pattern: everything under your home directory, managed with `systemctl --user`. No sudo required for daemon and backend. Frontend requires a one-time `next build`.

All three services are documented here so you can run Sheshnag hands-free on a worker machine or dev server.

## Prerequisites

Before running any of these:

1. **Clone the repo** (or have `scripts/install.sh` clone it for you):
   ```bash
   git clone https://github.com/gamekeepers/sheshnag.git ~/.sheshnag
   ```
   The repository was renamed `moonknight` → `sheshnag`. GitHub redirects the
   old URL, so existing clones keep working, but new clones should use the name
   above. Update an old clone with
   `git remote set-url origin https://github.com/gamekeepers/sheshnag.git`.
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

See [Running on startup](#4-running-on-startup) below for the full boot checklist.

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

Next.js in production runs `next build` once (ahead of time) then `next start`. The systemd unit wraps both steps: the `ExecStartPre` builds, then `ExecStart` serves on port 3000.

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

## 4. Running on startup

Two things have to be true for a unit to come up on boot:

1. **The unit is enabled** — `systemctl --user enable <unit>` links it into
   `default.target`, which every unit in this repo declares under `[Install]`.
2. **Linger is on for your user** — without it, your user's systemd instance is
   only started when you log in, so an enabled unit still waits for a login.

Enable everything you intend to run at boot, then turn on linger:

```bash
loginctl enable-linger "$USER"

systemctl --user daemon-reload
systemctl --user enable ollama gpu-daemon          # worker machine
systemctl --user enable sheshnag-backend sheshnag-frontend   # control plane
```

Use `enable --now` instead of `enable` to start the units in the same command
rather than issuing a separate `start`.

Ordering is already encoded in the units — `gpu-daemon` declares
`After=ollama.service` and `sheshnag-frontend` declares
`After=sheshnag-backend.service` — so you don't need to sequence the enables.
Note that `After=` only orders startup; it does not pull in a unit you forgot to
enable.

### Verify

Confirm linger is on and each unit is enabled:

```bash
loginctl show-user "$USER" --property=Linger    # expect Linger=yes
systemctl --user is-enabled gpu-daemon sheshnag-backend sheshnag-frontend
systemctl --user list-unit-files --state=enabled
```

The real test is a reboot. Afterwards, without logging in interactively:

```bash
systemctl --user status gpu-daemon sheshnag-backend sheshnag-frontend
```

Note that `sheshnag-frontend` runs `next build` in `ExecStartPre`, so it can sit
in `activating` for several minutes after boot before it reports `active` — that
is expected, and `TimeoutStartSec=900` in the unit allows for it.

### Troubleshooting boot-time starts

| Symptom | Cause | Fix |
|---|---|---|
| Unit only runs while you're logged in | Linger not enabled | `sudo loginctl enable-linger "$(whoami)"` |
| `is-enabled` reports `disabled` | Never enabled, or unit copied after the last `enable` | `systemctl --user enable <unit>` |
| Unit not found after copying it in | systemd hasn't rescanned unit files | `systemctl --user daemon-reload` |
| Starts but immediately fails at boot | Env file or venv path wrong, or a dependency (network, DB) not up yet | `journalctl --user -u <unit> -b` |

`journalctl --user -u <unit> -b` shows logs from the current boot only, which is
the quickest way to see why something failed to come up.

### Disabling startup

To stop a service from starting at boot while keeping it installed:

```bash
systemctl --user disable <unit>       # leave it running for now
systemctl --user disable --now <unit> # stop it as well
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
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /v1/ {
        # Direct backend API access (e.g. daemon calls)
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /workers/ {
        proxy_pass http://127.0.0.1:8000/workers/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Adjust port mappings and paths for your deployment. The backend serves on `:8000`, frontend on `:3000`.

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
