# Lend your GPU to Sheshnag

**Who this is for:** you have a Linux machine with a GPU, and someone running a
Sheshnag deployment has asked you to contribute it. About ten minutes.

*Verified against code: 2026-08-26.*

You do **not** need to clone this repository, know Python, install a database, or
have `sudo`. Everything lands under `~/.gpu-daemon/` in your own home directory,
and the installer refuses to run as root.

---

## What you are agreeing to

A small daemon runs on your machine. Every few seconds it asks the control plane
whether there is a batch of prompts waiting. If there is, it downloads the input
file, runs the prompts through a local inference runtime (Ollama by default),
uploads the results, and goes back to waiting.

**What it does to your machine**

- Uses your GPU when a job is running, and nothing when idle.
- Downloads model weights on demand — these can be large, and they land under
  your home directory.
- Runs two background services under your own user account.

**What it does not do**

- No root, no system-wide packages, no changes outside your home directory.
- It never accepts inbound connections. Every exchange is your machine calling
  out to the control plane.
- The operator cannot run arbitrary commands on your box. The daemon only
  executes inference prompts through the runtime.

You can stop lending at any time with one command — see
[Stop lending](#stop-lending).

---

## Before you start

| You need | Notes |
|---|---|
| **Linux** | The installer exits on anything else. |
| **Python 3.10+**, **git**, **curl** | Checked, not installed. If any is missing the installer stops and prints the package list to hand your admin. |
| **An NVIDIA GPU** | Optional but the entire point. Without `nvidia-smi` the daemon still runs and reports no GPU, so it will sit idle. |
| **The platform URL** | From whoever runs the deployment, e.g. `https://sheshnag.example.edu`. |
| **A worker key** (`gk-…`) | See below. |

!!! warning "Right now this needs repository access"
    The installer downloads itself from a URL and then clones this repository to
    fetch the daemon code. **The repository is private**, so both steps fail for
    anyone outside the organisation — the install URL returns `404` and the
    clone returns `401`.

    Until that is resolved you need either a GitHub account with access to
    `gamekeepers/sheshnag`, or the operator has to hand you the daemon another
    way. If you are the operator reading this, see
    [Making this work for outside providers](#making-this-work-for-outside-providers).

---

## 1. Get your worker key

Keys belong to an **organisation**, not to a person or a machine. One key can
register any number of workers.

1. Sign in to the dashboard and open the **Provider portal**.
2. Go to **Worker keys**.
3. **Generate worker key**, give it a name you will recognise later — usually the
   machine's name.
4. **Copy it now.** The key is shown exactly once and is never displayed again.
   If you lose it, generate another and revoke the old one.

It looks like `gk-` followed by a long random string. Treat it like a password:
anyone holding it can register workers into your organisation.

---

## 2. Install

One command, run as your ordinary user:

```bash
curl -fsSL https://<your-platform-host>/install.sh | bash
```

Ask the operator for the exact URL — it is served by their deployment, not by
GitHub.

It asks three questions:

| Prompt | Answer |
|---|---|
| Platform URL | The deployment's address, e.g. `https://sheshnag.example.edu` |
| Org worker API key | The `gk-…` key from step 1 |
| Worker ID | Press Enter — the control plane assigns one |

**No terminal?** For automation, set the answers as environment variables and
nothing is prompted:

```bash
BACKEND_URL=https://sheshnag.example.edu API_KEY=gk-... bash install.sh
```

### What it does, in order

Worth knowing, because it is your machine:

1. **Checks prerequisites** — `python3` (3.10+), `git`, `curl`. Reports
   `nvidia-smi` as a warning only, never a failure.
2. **Installs Ollama** into `~/.gpu-daemon/` if it is not already on your
   `PATH` — a user-local copy, not a system package.
3. **Writes `~/.gpu-daemon/config.yaml`** with your answers, `chmod 600` because
   it contains the key.
4. **Clones the daemon code** into `~/.gpu-daemon/src`.
5. **Creates a Python virtual environment** at `~/.gpu-daemon/venv` and installs
   the daemon's dependencies into it. Nothing touches your system Python.
6. **Registers two user services** — `gpu-daemon` and `ollama` — starts them, and
   enables *linger* so they survive logout.

Everything it creates lives in one directory:

```
~/.gpu-daemon/
├── bin/          user-local Ollama, if it installed one
├── src/          the daemon source
├── venv/         its Python environment
├── config.yaml   your settings — mode 600, holds the key
├── credentials   the key plus the worker id the backend assigned
└── jobs/         job inputs and outputs, transient
```

---

## 3. Check it worked

Three checks, in increasing order of confidence. Do all three the first time.

**The service is running:**

```bash
systemctl --user status gpu-daemon
```

Expect `Active: active (running)`.

**It registered and is polling:**

```bash
journalctl --user -u gpu-daemon -f
```

Within about thirty seconds you should see registration succeed and then a
steady poll every five seconds. Errors mentioning the API key mean the key is
wrong or revoked; errors about connecting mean the platform URL is wrong or the
host is unreachable from your machine.

**The dashboard can see you** — this is the one that actually proves it:

Provider portal → **Workers**. Your machine appears with its GPU and VRAM.
It heartbeats every thirty seconds; if it stops, the control plane marks it
offline and hands any batch it was running to another worker.

You are done. The machine will pick up work on its own.

---

## Everyday operations

```bash
systemctl --user status gpu-daemon      # is it alive
journalctl --user -u gpu-daemon -f      # what is it doing
systemctl --user restart gpu-daemon     # after editing config.yaml
systemctl --user stop gpu-daemon        # pause lending, keep everything installed
systemctl --user start gpu-daemon       # resume
```

**Take the machine back for a while** — stop the daemon. Any batch in flight is
requeued to another worker; nothing is lost.

**Update to a newer daemon:**

```bash
git -C ~/.gpu-daemon/src pull --ff-only
systemctl --user restart gpu-daemon
```

---

## Settings you might change

Edit `~/.gpu-daemon/config.yaml`, then restart the service. The file the
installer writes is deliberately minimal; these are the values worth knowing.

| Setting | Default | What it does |
|---|---|---|
| `backend_url` | — | The control plane. Set at install. |
| `api_key` | — | Your `gk-…` key. Set at install. |
| `runtime` | `ollama` | `ollama` or `vllm`. |
| `ollama_url` | `http://localhost:11434` | Where your Ollama is listening. |
| `vllm_url` | `http://localhost:8100` | Only when `runtime: vllm`. |
| `poll_interval` | `5` | Seconds between "any work?" checks. |
| `heartbeat_interval` | `30` | Seconds between liveness reports. |
| `inference_timeout` | `300.0` | Per-prompt ceiling, in seconds. |
| `log_level` | `INFO` | `DEBUG` when diagnosing. |

Every one of these can also be given as an environment variable — `DAEMON_` plus
the name in capitals, e.g. `DAEMON_LOG_LEVEL=DEBUG`. Precedence is command line,
then environment, then this file, then the built-in defaults.

The full list, including flags a provider rarely needs, is in the
[daemon README](https://github.com/gamekeepers/sheshnag/blob/develop/daemon/README.md).

---

## When something is wrong

**"No TTY available"** — you piped the installer into `bash` from somewhere
without a terminal. Re-run with `BACKEND_URL=` and `API_KEY=` set, as shown
above.

**"WARNING: nvidia-smi not found"** — the daemon installs and runs, but reports
no GPU and will never be given work. Install the NVIDIA drivers (an admin task)
and restart the service.

**"enable-linger failed"** — your distro requires an administrator for this. The
daemon works while you are logged in and stops when you log out. To fix
permanently, ask an admin to run `sudo loginctl enable-linger <your-user>`.

**Missing `python3`, `git` or `curl`** — the installer prints exactly what to
ask for: `sudo apt-get install -y python3 python3-venv python3-pip git curl`.

**Registration fails with an authentication error** — the key is wrong, or it
was revoked in the dashboard. Revoking a key stops every daemon using it
immediately. Generate a new one and update `config.yaml`.

**The worker shows offline but the service is running** — the machine cannot
reach the platform. Check the URL in `config.yaml` and whether your network
allows outbound HTTPS to that host.

**No systemd user session** — the installer says so and prints a `nohup` command
to run the daemon by hand instead. It will not survive a reboot.

---

## Stop lending

Pause, keeping everything installed:

```bash
systemctl --user stop gpu-daemon
```

Remove it completely:

```bash
systemctl --user disable --now gpu-daemon ollama
rm -f ~/.config/systemd/user/gpu-daemon.service ~/.config/systemd/user/ollama.service
systemctl --user daemon-reload
rm -rf ~/.gpu-daemon
```

Then revoke the key in **Provider portal → Worker keys** if no other machine of
yours is using it. Nothing is left outside your home directory, because nothing
was ever put there.

---

## Making this work for outside providers

*For operators, not providers.*

The install flow above assumes a provider can fetch two things: the installer
script, and the daemon source. Today neither is reachable without access to a
private repository — the raw script URL returns `404` and an anonymous clone
returns `401`. Verified 2026-08-26.

Until that is closed, one of these has to be true:

- **Serve the installer and the source yourself.** The script already expects
  this — its own usage line reads
  `curl -fsSL https://platform.example.com/install.sh | bash`. It also honours a
  `REPO_URL` environment variable, so pointing it at a mirror you host is a
  one-variable change.
- **Give the provider repository access**, which only works for people inside
  the organisation.
- **Make the repository public**, which resolves this and the documentation
  hosting question together.

This is tracked as a blocker against the documentation plan; it is a
distribution decision, not a docs one.
