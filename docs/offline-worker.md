# Lend a GPU offline

**Who this is for:** the machine you want to contribute cannot reach the
internet, or cannot run `systemctl --user`, or both. Institutional clusters are
usually all three — no route out, an old init, and a shared home directory.

*Verified against code: 2026-09-25.*

The ordinary path in [Lend your GPU](provider.md) assumes a host that can fetch
a script, download a runtime, and be supervised by systemd. Where those hold,
use it — this page exists for where they do not.

---

## What is different

| The usual path | Here |
|---|---|
| `curl … \| bash` fetches the installer | An archive is built elsewhere and carried in |
| The installer downloads Ollama | The runtime is built or copied in; llama.cpp is the usual choice |
| Models are pulled on demand | GGUF files are staged by hand |
| `systemctl --user` supervises | `cron` plus `flock` supervises |
| The daemon dials the control plane directly | It dials through a proxy or an SSH forward |

Everything else — the worker key, registration, how work is dispatched — is
unchanged. A worker installed this way is an ordinary worker.

!!! warning "Check the interpreter before anything else"
    A Python that cannot do HTTPS fails at registration with a TLS error rather
    than at install, which reads as a network fault and is not one:

    ```bash
    python3 -c "import ssl, sys; print(sys.version); print(ssl.OPENSSL_VERSION)"
    ```

    If that raises `ModuleNotFoundError: _ssl`, build the bundle with
    `WITH_PYTHON=1` below and use the interpreter it carries.

---

## 1. Build the bundle

On a machine that can reach PyPI and your repository host:

```bash
cd sheshnag
WITH_PYTHON=1 scripts/build-offline-bundle.sh
```

This writes `dist/sheshnag-daemon-offline-<date>.tar.gz` — the daemon as a
wheel, its dependencies, an interpreter, and an installer that needs no index.
About 70 MB with the interpreter, 5 MB without.

Dependencies resolve for the **target**, not the build host:

```bash
PLATFORM=manylinux2014_x86_64 ABI=cp312 PY_VERSION=3.12 \
    scripts/build-offline-bundle.sh
```

Those are the defaults, and they are a contract rather than a preference: a
host on glibc 2.17 cannot load a `manylinux_2_28` wheel, and a wheel built for
the wrong interpreter fails on import.

## 2. Carry it in and install

```bash
scp dist/sheshnag-daemon-offline-*.tar.gz worker:~/bundle.tgz
# on the worker
mkdir -p ~/bundle && tar -xzf ~/bundle.tgz -C ~/bundle
INSTANCE=$(hostname -s) ~/bundle/install.sh
```

`INSTANCE` matters when several machines mount the same home directory. Without
it they share one config, one virtual environment and — worst — one credentials
file holding a single worker id, so two machines heartbeat as one worker.

The same bundle updates an existing install. It carries software only:
configuration and whatever supervises the daemon are the host's own and are
left alone.

## 3. Stage the model files

The daemon does not download models here. Copy the GGUFs in, naming each file
after the `runtime_model_id` of a `llamacpp` profile in the model catalogue:

```
gpt-oss-20b.gguf        ← catalogue row gpt-oss-20b-mxfp4, llamacpp profile
llama3-2-3b.gguf        ← catalogue row llama3-2-3b-q4km
```

In router mode `llama-server` reports each file's stem as its model id. Name a
file anything else and the worker is online, healthy, and never dispatched to —
the same failure the `--alias` flag causes on a single-model server.

For a fleet behind a jump host, `scripts/stage-models.sh` takes a manifest of
`name path source` rows and copies them to each worker over one connection,
skipping files already present and resuming partial ones:

```
gemma4-26b    /var/models/gemma4-26b-q4km.gguf    unsloth/gemma-4-26B-GGUF
llama3-2-3b   /var/models/llama3.2-3b-q4km.gguf   unsloth/Llama-3.2-3B-GGUF
```

```bash
JUMP=user@jump-host scripts/stage-models.sh models.txt worker1 worker2
```

**Name the source.** A model's identity is the sha256 of its weights, and the
third column says which public repo those bytes came from. With both, the
platform confirms the model against that repo and adds it to the catalogue by
itself; without them the worker reports a model nobody can vouch for, and each
one needs a catalogue entry written by hand.

The script computes each hash where the file already is and ships a
`<name>.gguf.json` sidecar alongside, so a worker never reads back a
multi-gigabyte model to learn what it received. A file staged by other means
is hashed on the worker instead — once, at one file per heartbeat, with the
result written beside it.

!!! note "`/tmp` is swept"
    `tmpwatch` removes files under `/tmp` on a ten-day window and judges by
    access, modification and change times. A model staged for failover may go
    weeks without being loaded, and where `/tmp` is mounted `noatime` even one
    in daily use looks untouched. The setup script in the next section installs
    a nightly `touch` that keeps all three fresh.

## 4. Configure and supervise

```bash
BACKEND_URL=https://sheshnag.example.edu API_KEY=gk-... \
PROXY=socks5h://127.0.0.1:1080 TUNNEL_HOST=jump-host \
    bash scripts/setup-offline-worker.sh
```

It writes the config, a supervisor loop, a control script and the cron entries,
then starts everything. Useful settings:

| Variable | Default | Purpose |
|---|---|---|
| `INSTANCE` | `hostname -s` | Names this install; keeps machines apart on a shared home |
| `RUNTIME` | `llamacpp` | `ollama`, `vllm` or `llamacpp` |
| `MODELS_DIR` | `/tmp/gguf` | Directory of GGUFs for router mode; also written to the daemon config, which needs it to identify what it serves |
| `MODELS_MAX` | `1` | How many models may be resident at once |
| `THREADS` | `nproc`, capped at 16 | `llama-server -t` |
| `PROXY` | unset | `socks5h://…` or `http://…` for a host with no route out |
| `TUNNEL_HOST` | unset | SSH host to keep a SOCKS forward open through |
| `PROBE_URL` | `BACKEND_URL` | What the supervisor fetches through the forward to confirm it carries |

**`MODELS_MAX` counts models, not bytes.** Two 17 GB models resident on a 32 GB
host fills memory exactly and leaves nothing for the KV cache; with swap
configured that thrashes rather than fails, which is harder to diagnose. Size it
against the largest models you staged, not the average.

Prefer `socks5h` over `socks5`: the hostname is resolved at the proxy, so
certificate verification behaves as it would on a direct connection.

### Why cron rather than systemd

`systemctl --user` needs a user D-Bus, which systemd 219 does not provide and a
container often lacks. Check before assuming:

```bash
systemctl --user show-environment >/dev/null 2>&1 && echo yes || echo no
```

Where it answers `no`, the supervisor loop plus `@reboot` and a five-minute
cron entry gives the same three guarantees — start on boot, restart on failure,
one instance — with `flock` enforcing the last. The lock lives on local disk
because `flock` cannot take one over NFS, and a shared home would otherwise let
two machines supervise as one worker.

## 5. Check it worked

```bash
~/.gpu-daemon-<instance>/ctl.sh status
```

Reports the three processes, the models on disk, and which are resident, and —
where a tunnel is configured — whether the forward is carrying traffic. A
forward that fails the check leaves its error in
`~/.gpu-daemon-<instance>/tunnel.log`, the same place the supervisor records
rebuilds. Then:

```bash
grep -i 'serves\|register' ~/.gpu-daemon-<instance>/daemon.log | tail -5
```

A worker id proves the whole chain — the forward, TLS, and the key. Finally,
**Provider portal → Workers**, which is the check that actually counts.

With `--models-autoload` the router loads nothing until a request arrives, so
every model reading `unloaded` at this point is correct rather than a fault.

---

## Operating it

| | |
|---|---|
| Status | `ctl.sh status` |
| Follow the daemon | `ctl.sh logs` |
| Follow the model server | `ctl.sh llama` |
| Restart the daemon | `ctl.sh restart` — the supervisor brings it back in ~10s |
| Stop lending | `ctl.sh stop` — also removes the cron entries |
| Update | rebuild the bundle, carry it in, re-run `install.sh`, then `ctl.sh restart` |

Adding a model later is one file copied into `MODELS_DIR`. The router picks it
up, so nothing needs reinstalling or restarting.

## When something is wrong

**Registration fails with a name-resolution error** — the daemon is not using
the proxy. Confirm `ALL_PROXY` is in `~/.gpu-daemon-<instance>/.env`, and that
the forward carries: `ctl.sh status` reports `tunnel carrying traffic` or
`tunnel DOWN`. A bound port only proves ssh is listening, not that the forward
answers requests; where it says `DOWN`, the failure is in
`~/.gpu-daemon-<instance>/tunnel.log`.

**Registration fails with a TLS error** — usually the interpreter, not the
network. See the warning at the top of this page.

**The worker is online but never given work** — the names do not match. Compare
`ls MODELS_DIR` against the `runtime_model_id` of each `llamacpp` profile in
the catalogue.

**Models vanish after a week or two** — `/tmp` was swept. The nightly `touch`
prevents it; check it survived with `crontab -l`.

**Two machines appear as one worker** — they share a home and an install
directory. Reinstall each with a distinct `INSTANCE`.

## See also

- [Lend your GPU](provider.md) — the ordinary path, for hosts with a route out
- [Configuration](reference/configuration.md) — every variable, including the proxy ones
- [Daemon internals](reference/daemon.md) — what the daemon does once it is running
