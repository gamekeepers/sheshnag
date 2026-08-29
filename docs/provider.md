# Lend your GPU to Sheshnag

**Who this is for:** you have a Linux machine with a GPU, and someone running a
Sheshnag deployment has asked you to contribute it. About ten minutes.

*Verified against code: 2026-08-26.*

You do **not** need to clone this repository, know Python, install a database, or
have `sudo`. Everything lands in one directory in your own home — `~/.gpu-daemon/`,
or a host-specific one where several machines share a home — and the installer
refuses to run as root.

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
- Runs one or two background services under your own user account — the daemon,
  and Ollama as well if you did not already have it.

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

Nothing else — no GitHub account, no clone, no permissions from anyone. Sheshnag
is open source under the Apache License 2.0, so the installer and the daemon
source are public.

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
curl -fsSL https://raw.githubusercontent.com/gamekeepers/sheshnag/develop/scripts/install.sh | bash
```

Some deployments serve their own copy at `https://<their-host>/install.sh` — if
the operator gave you a URL, use theirs. Both fetch the same script.

!!! tip "Read it first if you like"
    Piping a script into `bash` deserves scepticism. Drop the `| bash` to read
    it — it is about 150 lines, installs only under `~/.gpu-daemon/`, and exits
    if you run it as root.

It asks four questions, all at the start, so nothing waits on you once the
install is running:

| Prompt | Answer |
|---|---|
| Install directory | Press Enter — the default is `~/.gpu-daemon`, or a host-specific one if your home looks shared |
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
2. **Asks its questions and writes `~/.gpu-daemon/config.yaml`**, `chmod 600`
   because it contains the key. Everything interactive happens here, before the
   slow steps, so you are not called back to a prompt ten minutes later.
3. **Installs Ollama** into `~/.gpu-daemon/` if it is not already on your
   `PATH` — a user-local copy, not a system package.
4. **Clones the daemon code** into `~/.gpu-daemon/src`.
5. **Creates a Python virtual environment** at `~/.gpu-daemon/venv` and installs
   the daemon's dependencies into it. Nothing touches your system Python.
6. **Registers user services**, starts them, and enables *linger* so they
   survive logout. Always `gpu-daemon`; plus `ollama` **only** when step 3
   installed a user-local copy. If your machine already had Ollama, the
   installer leaves it alone rather than starting a second copy to fight it
   for port 11434.
7. **Reports what systemd actually did.** The last line tells you whether
   `gpu-daemon` came up, rather than claiming success regardless.

Everything it creates lives in one directory:

```
~/.gpu-daemon/
├── bin/          user-local Ollama, if it installed one
├── src/          the daemon source
├── venv/         its Python environment
├── config.yaml   your settings — mode 600, holds the key
├── credentials   the key plus the worker id the backend assigned
├── installed-by  which machine owns this directory
└── jobs/         job inputs and outputs, transient
```

### Several machines, one home directory

Clusters commonly mount the same home directory on every node. An install
belongs to one machine, so on a shared home each machine takes its own
directory and its own service name.

The installer picks the right one for you. On a local disk it uses
`~/.gpu-daemon/`. On a networked filesystem — NFS, CIFS, Lustre, GPFS, BeeGFS,
Ceph — it offers a host-specific directory instead, and Enter accepts it:

```
[2/6] Configuration
Your home directory is on nfs, so it is probably shared between machines.
Install directory [~/.gpu-daemon-gpubox1]:
```

Everything that machine owns follows that directory — its config, credentials,
virtual environment and job files — and so does the unit name, because
`~/.config/systemd/user/` is shared too. Manage it under that name:

```bash
systemctl --user status gpu-daemon-gpubox1
journalctl --user -u gpu-daemon-gpubox1 -f
```

Type any directory you like at the prompt, or name the machine up front, which
is also the form to use when there is no terminal:

```bash
INSTANCE=$(hostname -s) bash install.sh
```

Each directory records its owner in `installed-by`, and the installer stops if
you point a second machine at a directory that already belongs to one.

Separate directories are what keep the two workers distinct. The control plane
assigns a worker id per machine and the daemon keeps it in `credentials`; one
file shared between machines would eventually have both answering as the same
worker, so each keeps its own.

Your machines appear separately in the dashboard either way — registration
identifies a worker by hostname, so that part needs nothing from you.

!!! note "Model weights are shared too"
    Ollama stores weights in `~/.ollama`, and two Ollama servers should not
    write to one store. Either set `OLLAMA_MODELS` per machine, or point every
    daemon at a single host's Ollama with `ollama_url` in its config.

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
[Daemon internals](reference/daemon.md).

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

On a machine installed under its own name, substitute it throughout — the
service is `gpu-daemon-<name>` and the directory `~/.gpu-daemon-<name>`. The
completion banner and `systemctl --user list-units 'gpu-daemon*'` will both tell
you which you have.

Then revoke the key in **Provider portal → Worker keys** if no other machine of
yours is using it. Nothing is left outside your home directory, because nothing
was ever put there.

---

## Serving the installer yourself

*For operators, not providers.*

You do not have to. Sheshnag is public, so the command in
[Install](#2-install) works for anyone, and most operators can simply hand a
provider that line plus a key.

Two reasons you might still host your own copy:

- **You want providers pinned to a version you have tested**, rather than to
  whatever is on `develop` the day they install.
- **Your providers are on a network that cannot reach GitHub**, which is common
  enough on institutional machines.

Both are the same fix: serve the script from the host that already serves the
dashboard, next to the docs at `/docs/` — see
[Serve the documentation](self-host.md#serve-the-documentation). The installer
expects this; its own usage line reads
`curl -fsSL https://platform.example.com/install.sh | bash`. To pin the source
as well, set `REPO_URL` to your mirror and, if you want a fixed version, a tag
rather than a branch.
