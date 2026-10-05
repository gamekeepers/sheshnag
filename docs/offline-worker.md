# Lend a GPU offline

**Who this is for:** the machine you want to contribute cannot reach the
internet, or cannot run `systemctl --user`, or both. Institutional clusters are
usually all three — no route out, an old init, and a shared home directory.

*Verified against code: 2026-09-25.*

The ordinary path in [Lend your GPU](provider.md) assumes a host that can fetch
a script, download a runtime, and be supervised by systemd. Where those hold,
use it — this page exists for where they do not.

---

## TL;DR — what you are about to do

**Build everything on a machine that has the internet and a compiler. The worker only ever
receives finished files**, and nothing done to it needs `sudo`.

| # | Step | Runs on | The command |
|---|---|---|---|
| 1 | [Build the bundle](#1-build-the-bundle) | Build host | `WITH_PYTHON=1 scripts/build-offline-bundle.sh` |
| 2 | [Build the runtime](#2-build-the-inference-runtime) | Build host, in Docker | `docker run … manylinux2014_x86_64` |
| 3 | [Carry it in and install](#3-carry-it-in-and-install) | Build host → worker | `~/bundle/install.sh` |
| 4 | [Stage the models](#4-stage-the-model-files) | Build host → worker | Any copy — `scp`, `rsync`, `stage-models.sh` |
| 5 | [Configure and supervise](#5-configure-and-supervise) | Worker | `~/bundle/setup-offline-worker.sh` |
| 6 | [Check it worked](#6-check-it-worked) | Worker | `ctl.sh status` |

Every section says which machine it runs on — **the build host**, **the worker**, or a
transfer between them. Commands that only report state, and change nothing, sit in a
**Check** box.

---

## What is different

| The usual path | Here |
|---|---|
| `curl … \| bash` fetches the installer | An archive is built elsewhere and carried in |
| The installer downloads Ollama | The runtime is built elsewhere and carried in; llama.cpp is the usual choice ([step 2](#2-build-the-inference-runtime)) |
| Models are pulled on demand | GGUF files are staged by hand |
| `systemctl --user` supervises | `cron` plus `flock` supervises |
| The daemon dials the control plane directly | It dials through a proxy or an SSH forward |

Everything else — the worker key, registration, how work is dispatched — is
unchanged. A worker installed this way is an ordinary worker.

!!! warning "Check — on the worker, before anything else"
    A Python that cannot do HTTPS fails at registration with a TLS error rather
    than at install, which reads as a network fault and is not one:

    ```bash
    python3 -c "import ssl, sys; print(sys.version); print(ssl.OPENSSL_VERSION)"
    ```

    If that raises `ModuleNotFoundError: _ssl`, build the bundle with
    `WITH_PYTHON=1` below and use the interpreter it carries.

---

## 1. Build the bundle

**On the build host** — it needs to reach PyPI and your repository host.

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

## 2. Build the inference runtime

The daemon attaches to a runtime and never builds or starts one, so the binary
is yours to produce. Upstream's
[build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
covers building llama.cpp on a machine that will also run it. The case this page
exists for is the other one: the target has no compiler, and the binary has to
run on a host older than whatever built it.

**On the build host**, inside a glibc 2.17 image, so nothing in the result needs
a glibc newer than the target carries:

```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp
mkdir -p out
cat > build.sh <<'EOF'
set -e
export PATH=/opt/python/cp312-cp312/bin:$PATH
pip install -q cmake ninja
cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF \
  -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON \
  -DGGML_OPENMP=OFF -DGGML_BLAS=OFF -DGGML_CUDA=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON
cmake --build /build -j"$(nproc)" --target llama-server llama-cli
cp /build/bin/llama-server /build/bin/llama-cli /out/
EOF
docker run --rm \
  -v "$PWD/llama.cpp:/src:ro" -v "$PWD/out:/out" -v "$PWD/build.sh:/build.sh:ro" \
  quay.io/pypa/manylinux2014_x86_64 bash /build.sh
```

Those flags are a contract, and each one is a failure that lands on the target
while the build host stays silent:

| Flag | What it keeps out of the binary |
|---|---|
| `GGML_NATIVE=OFF`, with `AVX`, `AVX2`, `FMA` and `F16C` named | Instructions detected from the build machine's own CPU. An older target traps on them, and it arrives as `Illegal instruction` on the first prompt, not at startup |
| `-static-libstdc++ -static-libgcc`, `BUILD_SHARED_LIBS=OFF` | A dependency on a C++ runtime newer than the target's, which cannot be resolved at load |
| `GGML_OPENMP=OFF`, `GGML_BLAS=OFF` | Shared libraries the target has no copy of |
| `LLAMA_CURL=OFF` | A libcurl linked against a TLS stack the target does not carry |

**Name the instruction sets your oldest machine has**, not the newest. The set
above is a Haswell floor; a fleet containing anything older serves that machine
a binary it cannot execute. `GGML_CUDA` is the one flag that follows the
individual machine, not the fleet — turn it on only where the driver is new
enough for the card it drives, since a driver older than the card's compute
capability leaves the GPU reporting itself and serving nothing.

!!! tip "Check — on the build host"
    Assert the result before carrying it anywhere. The highest glibc symbol the
    binary references must not exceed what the target provides:

    ```bash
    objdump -T out/llama-server | grep -o 'GLIBC_2\.[0-9]*' | sort -uV | tail -1
    ```

Carry the binary in and put it where the supervisor looks.

**Build host → worker**

```bash
tar -czf llama-bin.tar.gz -C out llama-server llama-cli
scp llama-bin.tar.gz worker:~/
```

**On the worker**

```bash
mkdir -p ~/opt/llama/bin && tar -xzf ~/llama-bin.tar.gz -C ~/opt/llama/bin
```

`~/opt/llama/bin/llama-server` is where `setup-offline-worker.sh` expects it;
`LLAMA_BIN` names any other path.

## 3. Carry it in and install

**Build host → worker**

```bash
scp dist/sheshnag-daemon-offline-*.tar.gz worker:~/bundle.tgz
```

**On the worker**

```bash
mkdir -p ~/bundle && tar -xzf ~/bundle.tgz -C ~/bundle
INSTANCE=$(hostname -s) ~/bundle/install.sh
```

`INSTANCE` matters when several machines mount the same home directory. Without
it they share one config, one virtual environment and — worst — one credentials
file holding a single worker id, so two machines heartbeat as one worker.

The same bundle updates an existing install. It carries software only — the
daemon, this installer and the supervisor script step 5 runs — while
configuration is the host's own and is left alone.

## 4. Stage the model files

The daemon downloads nothing here, so the GGUFs arrive by whatever means the
machine already gives you — `scp`, `rsync`, a USB disk, or a share it mounts.
**Only the end state matters.**

**On the worker, once the copy is done**

| | |
|---|---|
| Where | `MODELS_DIR` — `/tmp/gguf` by default |
| Named | `<runtime_model_id>.gguf`, from a `llamacpp` profile in the catalogue |
| Beside it, optionally | `<runtime_model_id>.gguf.json`, declaring the file's hash and origin |

**File names** — not a command, a contract:

```
gpt-oss-20b.gguf        ← catalogue row gpt-oss-20b-mxfp4, llamacpp profile
llama3-2-3b.gguf        ← catalogue row llama3-2-3b-q4km
```

In router mode `llama-server` reports each file's stem as its model id. Name a
file anything else and the worker is online, healthy, and never dispatched to —
the same failure the `--alias` flag causes on a single-model server.

### Declaring what a file is

A model's identity is the sha256 of its weights. **A sidecar is optional.**
Without one the worker hashes the file itself, once, at one file per heartbeat,
and writes the result beside it — a slow first heartbeat on a large model and
nothing worse.

A sidecar spares that read, and its `source_ref` names the public repo the bytes
came from, which lets the platform confirm the model and add it to the catalogue
by itself. With no origin the worker reports a model nobody can vouch for, and
each entry has to be written by hand.

```json
{"sha256": "9f2e…", "size": 20401094656, "source_ref": "unsloth/gpt-oss-20B-GGUF"}
```

**`size` has to equal the file's own size**, or the sidecar is ignored and the
file is hashed as though it were absent — which is what keeps a sidecar that
outlived its bytes from publishing one artifact's identity for another's.

### Staging several workers at once

`scripts/stage-models.sh` does the copy and the sidecar over a single
connection, skipping files already present and resuming partial ones. It reaches
the workers through a jump host, which is the case it exists for.

`models.txt` — one row per model, `name path source`, paths absolute:

```
gemma4-26b    /var/models/gemma4-26b-q4km.gguf    unsloth/gemma-4-26B-GGUF
llama3-2-3b   /var/models/llama3.2-3b-q4km.gguf   unsloth/Llama-3.2-3B-GGUF
```

**Build host → worker**

```bash
JUMP=user@jump-host scripts/stage-models.sh models.txt worker1 worker2
```

!!! note "`/tmp` is swept"
    `tmpwatch` removes files under `/tmp` on a ten-day window and judges by
    access, modification and change times. A model staged for failover may go
    weeks without being loaded, and where `/tmp` is mounted `noatime` even one
    in daily use looks untouched. The setup script in the next section installs
    a nightly `touch` that keeps all three fresh.

## 5. Configure and supervise

**On the worker**

```bash
BACKEND_URL=https://sheshnag.example.edu API_KEY=gk-... \
PROXY=socks5h://127.0.0.1:1080 TUNNEL_HOST=jump-host \
    bash ~/bundle/setup-offline-worker.sh
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
container often lacks.

!!! tip "Check — on the worker"
    ```bash
    systemctl --user show-environment >/dev/null 2>&1 && echo yes || echo no
    ```

Where it answers `no`, the supervisor loop plus `@reboot` and a five-minute
cron entry gives the same three guarantees — start on boot, restart on failure,
one instance — with `flock` enforcing the last. The lock lives on local disk
because `flock` cannot take one over NFS, and a shared home would otherwise let
two machines supervise as one worker.

## 6. Check it worked

**On the worker**

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
