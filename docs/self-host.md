# Run Sheshnag for your institution

**Who this is for:** you are standing Sheshnag up on hardware your organisation
controls, so that your researchers can submit batches and your GPUs can serve
them. Budget an afternoon for the first one.

*Verified against code: 2026-08-26. The database, service and TLS sections are
carried over unchanged from the former `setup.md` and `services.md`, which were audited
against live code on 2026-08-02.*

---

## What you are standing up

Three pieces, and only the first two are the control plane:

| Piece | Port | What it is |
|---|---|---|
| **Backend** | 8000 | FastAPI. Validates batches, schedules them, tracks workers, serves results. Owns the database. |
| **Frontend** | 3000 | Next.js dashboard. What your users and providers log into. |
| **Worker daemon** | — | Runs on GPU machines, not usually this one. Polls the backend for work. |

The control plane needs **no GPU**. It needs Postgres, a domain, and a
certificate. GPUs arrive later, one provider at a time, and each one installs
itself — see [Lend your GPU](provider.md).

You are done when: the dashboard is reachable over HTTPS, the default admin
password is changed, the model catalogue is loaded, and one worker has connected
and claimed a job.

## Before you start

| You need | Notes |
|---|---|
| **A Linux host** | Everything below is rootless except TLS. |
| **Python 3.10+**, **Node.js 20+**, **npm 10+** | Backend and frontend respectively. |
| **PostgreSQL 14+** | A reachable server and an account on it. You do **not** need to own or administer it — see [Create the database](#1-create-the-database). |
| **A domain and DNS** | Pointing at this host, so certbot can issue a certificate. |
| **Administrator help, once** | For nginx, the certificate, and `loginctl enable-linger`. Nothing else needs root. |

If you only want to try it on a laptop, you do not need this guide — the
localhost path in [Work on Sheshnag](develop.md) is shorter and skips TLS entirely.

## 1. Create the database


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

### A. You can create databases (`rolcreatedb` = `t`)

The normal case, and the one to prefer — the app gets a database it owns.

```bash
createdb -h HOST -p PORT -U USER sheshnag
```

```ini
# backend/.env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/sheshnag
```

### B. You cannot create databases (`rolcreatedb` = `f`)

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

### Confirm before moving on

Connection problems are the most common first-run failure, and they are much
easier to read here than in a uvicorn traceback:

```bash
psql "postgresql://USER:PASSWORD@HOST:PORT/DATABASE" -c '\conninfo'
```

### Resetting

There is no migration tool, so dropping and recreating is also how you pick
up a schema change. Match it to the path you used:

```bash
dropdb -h HOST -p PORT -U USER sheshnag && createdb -h HOST -p PORT -U USER sheshnag   # path A
psql "$DATABASE_URL" -c "DROP SCHEMA sheshnag CASCADE; CREATE SCHEMA sheshnag;"        # path B, schema
```


## 2. Configure

Two env files, one per component. Copy the examples and edit:

```bash
cp .env.example .env.local              # frontend
cp backend/.env.example backend/.env    # backend
```

The full variable reference lives in
[Configuration](reference/configuration.md). These
are the ones that separate a deployment from a laptop:

| Variable | File | Why it matters here |
|---|---|---|
| `SECRET_KEY` | `backend/.env` | Signs every session token. **Generate one:** `openssl rand -hex 32`. The shipped default is for development; the backend logs a warning at startup if you leave it. |
| `DATABASE_URL` | `backend/.env` | From [section 1](#1-create-the-database). |
| `CORS_ORIGINS` | `backend/.env` | Defaults to `*`, which is wrong the moment the service is reachable. Set it to your dashboard's URL. |
| `GOOGLE_CLIENT_ID` | `backend/.env` | Must be **identical** to `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in the frontend file, and registered against your production domain. |
| `NEXT_PUBLIC_BACKEND_URL` | `.env.local` | Your HTTPS URL, not `localhost` — the browser calls this directly. |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | `.env.local` | See above. |
| `FRONTEND_URL` | `backend/.env` | Used to build password-reset and invite links. Wrong value here means users get emails pointing at localhost. |
| `MAILGUN_*` | `backend/.env` | Optional. Without them, password resets and invitations are silently skipped — which on a real deployment means users cannot recover accounts. |

!!! warning "The frontend bakes its variables in at build time"
    `NEXT_PUBLIC_*` values are compiled into the bundle by `next build`, not read
    at runtime. Editing `.env.local` does nothing until the frontend is rebuilt —
    and its systemd unit rebuilds on restart, so `systemctl --user restart
    sheshnag-frontend` is how you apply a change.

Google sign-in has its own setup — see
[Google OAuth](reference/google-oauth.md).
## 3. First start, and securing the admin account

On first startup `Base.metadata.create_all()` creates every table and the
model catalogue is seeded. A default superadmin is created too:
`admin@platform.com` / `admin`. You will be asked to change the password on
first login.

**Change that password immediately.** Until you do, anyone who can reach the
dashboard has superadmin. The forced prompt on first login is the only thing
standing in front of it.

## 4. Run it as services

Everything in this section is rootless — `systemctl --user`, no sudo. Only
[section 6](#6-put-it-behind-tls) needs an administrator.

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

See [Start on boot](#5-start-on-boot) below for the full boot checklist.

### Backend

The backend (FastAPI + uvicorn) needs a virtual environment and an env file with at least `SECRET_KEY`, `DATABASE_URL`, and `GOOGLE_CLIENT_ID`.

#### Create the unit

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

#### Setup checklist for backend service

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

#### Production note: CORS

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

See the [environment variable reference](reference/configuration.md#backend-backend) in the configuration
guide for the full list.

### Frontend

Next.js in production runs `next build` once (ahead of time) then `next start`. The systemd unit wraps both steps: the `ExecStartPre` builds, then `ExecStart` serves on port 3000.

#### Create the unit

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

#### Setup checklist for frontend service

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

### Optional — a worker on this machine

Only if the control-plane host also has a GPU you intend to lend. Most
institutions run workers on separate machines; see
[Lend your GPU](provider.md) for the provider-side install.


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

#### Configuration layers (highest → lowest)

1. CLI args passed in the unit's `ExecStart`
2. Environment variables (`DAEMON_*`) set in `~/.gpu-daemon/.env`
3. YAML config: `~/.gpu-daemon/config.yaml`
4. Defaults (see [Configuration](reference/configuration.md#daemon-daemon))

The unit reads `EnvironmentFile=-%h/.gpu-daemon/.env` so you can override any daemon setting without editing the unit.

#### Ollama runtime as a user service

If the worker machine doesn't have Ollama installed, install it rootlessly via `scripts/install.sh` which places a user-local binary at `~/.gpu-daemon/bin/ollama`. The companion unit `scripts/ollama.service` runs it:

```bash
cp scripts/ollama.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ollama
```

The gpu-daemon unit lists `After=ollama.service` so Ollama starts first.

## 5. Start on boot


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

## 6. Put it behind TLS

Everything above this point runs without root. The items below need an
administrator, and they are what make the deployment reachable by anyone other
than you.

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

    # This documentation, built as static HTML and served alongside the
    # product. See "Serve the documentation" below.
    #
    # The exact-match redirect matters: `location /docs/` does not match a
    # bare `/docs`, which would otherwise fall through to the frontend and
    # 404 — the one URL people type by hand.
    location = /docs { return 301 /docs/; }

    location /docs/ {
        alias /home/sheshnag/.sheshnag/site/;
        index index.html;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Adjust port mappings and paths for your deployment. The backend serves on `:8000`, frontend on `:3000`.

### Serve the documentation

**Your deployment serves its own copy of these docs**, at `/docs/`. That is
deliberate, and it is the answer to a question the documentation plan left open.

Two reasons it works this way rather than from one central website:

- **The docs match the code you are actually running.** A deployment on an older
  version serves that version's documentation. A single central site would
  describe whatever is newest, which is not what your users have.
- **It is the only channel that reaches your providers and batch users.** They
  cannot clone the repository — most of them will never see it. What they do
  have is your URL, because you gave it to them.

Build the site once, from the repository you already cloned:

```bash
python3 -m venv .venv-docs
.venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs build          # writes ./site/
```

`site/` is plain static HTML — no process to run, nothing to keep alive. Rebuild
it whenever you pull:

```bash
git pull --ff-only
.venv-docs/bin/mkdocs build
```

The `alias` path in the nginx block above must point at that `site/` directory.
If your repository lives somewhere other than `~/.sheshnag/`, adjust it — and
note that nginx's worker user needs read access along the whole path, which is
the usual reason a fresh `/docs/` returns 403.

Once it is up, hand people deep links rather than the repository:
`https://your-host/docs/provider/` is the provider guide,
`https://your-host/docs/` is the audience fork.

**The dashboard already links here.** Each portal carries a **Documentation**
entry in its sidebar footer, pointed at the guide for that audience — the user
portal at [Submit batches](using-sheshnag.md), the provider portal at
[Lend your GPU](provider.md), the admin portal at this page — and the worker-key
screen deep-links to the install command at the moment an operator is handing a
key over. All of them resolve against `/docs/` on your own host, so building the
site is what makes them work; skip the build and they lead to a 404.

If you would rather not build the site, point the frontend elsewhere instead of
leaving the links broken:

```bash
# in your frontend .env.local, then rebuild
NEXT_PUBLIC_DOCS_URL=https://sheshnag.io/
```

That is a deliberate trade: the links work immediately, but they describe the
newest published version rather than the one you deployed. See
[`NEXT_PUBLIC_DOCS_URL`](reference/configuration.md) — like every
`NEXT_PUBLIC_*` value it is baked in at build time, so changing it means
`npm run build`, not a restart.

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


## 7. Load models, then onboard a provider

A deployment with no catalogue entries can accept batches but can never run
them: `body.model` in a batch must name a **catalogue entry**, and there is
deliberately no path to run an uncatalogued model.

1. **Load the catalogue.** Curation, pinning and digests are covered in
   [Model catalogue](reference/model-catalogue.md). A default set is seeded on
   first startup; anything beyond it is your decision.
2. **Create an organisation** in the dashboard, if the one your users belong to
   does not exist yet.
3. **Generate a worker key** — Provider portal → Worker keys. It is shown
   exactly once.
4. **Hand the provider that key and your platform URL**, and point them at
   [Lend your GPU](provider.md). They need nothing else from you.

## Check it worked

In order. Each one depends on the last.

**Services are up:**

```bash
systemctl --user status sheshnag-backend sheshnag-frontend
```

Both `active (running)`. The frontend may sit in `activating` for some minutes
on a cold start while `next build` runs — that is expected.

**The dashboard answers over HTTPS:** open your domain in a browser. A
certificate warning here means certbot has not run or nginx is serving the wrong
`server_name`.

**The admin password is no longer `admin`.** Sign in as
`admin@platform.com` and complete the forced change if you have not already.

**A worker connects:** have one provider run the installer, then check Provider
portal → **Workers**. The machine appears with its GPU and VRAM, and heartbeats
every thirty seconds.

**The docs are served:** open `https://your-host/docs/`. A 404 means the nginx
`alias` path is wrong or `mkdocs build` has not run; a 403 means nginx cannot
read along that path.

**A batch completes end to end:** submit a small JSONL batch from the dashboard
and watch it reach `completed`. This is the only check that exercises the whole
chain — validation, scheduling, a worker claiming it, results coming back.

Until that last one passes, you have a deployment that is running, not a
deployment that works.

## Production checklist


Before deploying to production, address each item and note whether it requires a code change or just configuration.

- [ ] **`SECRET_KEY`** — set a strong random value (e.g., `openssl rand -hex 32`). At startup the app logs a WARNING if the default is still in use.
- [ ] **Database** — provision the role and database as in [Create the database](#1-create-the-database), then point `DATABASE_URL` at it. The schema is created by `Base.metadata.create_all()` on first startup; there is no migration tool, so an existing database is never altered in place. Set up backups before the first real batch lands.
- [ ] **CORS origins** — set `CORS_ORIGINS` to your frontend URL(s) instead of `"*"`. Also review `allow_credentials=True` — browsers reject wildcard origins with credentials enabled, so you must list concrete origins when using cookies or auth headers.
- [ ] **HTTPS** — put a reverse proxy (Nginx, Caddy) in front of both frontend and backend. See [Put it behind TLS](#6-put-it-behind-tls).
- [ ] **Google OAuth** — register your production domain with Google Cloud Console. Set the same `GOOGLE_CLIENT_ID` in both `.env` and `backend/.env`.
- [ ] **Email (Mailgun)** — configure `MAILGUN_*` vars for password reset, invites, and notifications. Gracefully skipped if unset, but you lose email functionality.
- [ ] **Reverse proxy** — route `/api/` to backend :8000, `/` to frontend :3000. See [Put it behind TLS](#6-put-it-behind-tls).
- [ ] **Documentation** — run `mkdocs build` and serve `site/` at `/docs/`, so your providers and users have something to read that matches what you deployed. See [Serve the documentation](#serve-the-documentation).

## See also

- [Lend your GPU](provider.md) — hand this to every provider you onboard
- [Model catalogue](reference/model-catalogue.md) — curation and pinning
- [Google OAuth](reference/google-oauth.md) — sign-in setup
- [Data model](reference/data-model.md) — what the database holds
- [Work on Sheshnag](develop.md) — the development path
- [Configuration](reference/configuration.md) — every environment variable
