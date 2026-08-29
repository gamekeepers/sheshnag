# Configuration

Every environment variable the platform reads at runtime, across all three
components. Defaults are taken from live code.

*Verified against code: 2026-08-26. Carried over from `setup.md`, which was
audited on 2026-08-02.*

This is a reference — look things up here. For how to configure a real
deployment in the right order, see
[Host your deployment](../self-host.md#2-configure).

Every variable the platform reads at runtime. Defaults are pulled from live code.

### Frontend (`app/`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | no | `http://localhost:8000` | Backend API base URL |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | if Google OAuth | _(must set)_ | Google OAuth client ID |
| `NEXT_PUBLIC_NGROK_ENABLED` | no | unset / falsy | Set `"true"` behind ngrok tunnels to skip browser warnings |
| `NEXT_PUBLIC_DOCS_URL` | no | `/docs/` | Target of the in-app **Documentation** links. The default assumes this deployment serves its own copy — see [Serve the documentation](../self-host.md#serve-the-documentation). Set it to an external copy if you do not build the site. |

`NEXT_PUBLIC_*` values are **inlined at build time**, not read at runtime. Changing one means rebuilding the frontend (`npm run build`), not just restarting it.

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
| `DAEMON_VRAM_GB` | 0.0 | Advertised GPU memory in GB. Overrides detection — see below |

#### How a worker's VRAM is determined

The scheduler filters a worker out of any batch whose model needs more VRAM
than the worker reports, so this number decides what the worker is offered.

1. **`DAEMON_VRAM_GB`**, when set above 0, wins over everything — in
   registration and in every heartbeat — so a provider can lend less than the
   hardware holds. If nothing was probed, registration advertises one
   synthetic GPU (`vendor: other`, name from `DAEMON_GPU_NAME`) of that size;
   if several GPUs were probed, their sizes are scaled to sum to the
   declaration.
2. **NVIDIA and AMD**, otherwise: `nvidia-smi` and `rocm-smi` are both
   probed and their GPUs summed, so a mixed-vendor host advertises its whole
   capacity. AMD hosts also report a ROCm version, read from
   `/opt/rocm/.info/version` — not the amdgpu kernel module version
   `rocm-smi` prints, which is a different number.
3. **Apple Silicon**: Metal's `recommendedMaxWorkingSetSize`, the cap macOS
   places on how much unified memory the GPU may wire. Requires
   `pyobjc-framework-Metal` (installed automatically on macOS). Without it,
   `iogpu.wired_limit_mb` is used when explicitly set, otherwise a
   deliberately conservative fraction of `hw.memsize`. Detection stops here
   on an Apple Silicon Mac; the SMI tools are not probed.
4. **Otherwise 0.0**, and the worker is offered nothing.

That last case is the one to watch: a host with none of `nvidia-smi`,
`rocm-smi` or Metal — an Intel Mac, an Intel Arc box, CPU-only — registers
cleanly, heartbeats cleanly, and shows **online** in the dashboard while
never being handed a single batch. The daemon logs one warning
(`Advertising 0 GB VRAM …`) when this happens. Set `DAEMON_VRAM_GB` there.

> **Apple Silicon note.** There is no dedicated VRAM; CPU and GPU share one
> memory pool. The reported figure is macOS's *permission ceiling* (about
> 17.8 GB on a 24 GB machine), not free memory — the two differ whenever
> anything else on the machine is using RAM. `vram_available_gb` is
> therefore reported as *unknown* (`null`, shown as "—") on macOS: unified
> memory exposes no machine-wide "GPU memory in use" counter, and a confident
> "fully free" would be wrong whenever a model is resident. The scheduler
> reads only the total today. Intel Macs are not detected (no unified-memory
> ceiling); set `DAEMON_VRAM_GB` there.

See [Daemon internals](daemon.md) for the architecture and the backend contract.
