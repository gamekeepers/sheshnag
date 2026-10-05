# Worker daemon

Runs on a GPU machine. Polls the control plane for batch inference jobs,
executes them through a local runtime (**Ollama** by default, or **vLLM**),
uploads the results, and heartbeats throughout.

Nothing here needs root. Everything it installs lives under `~/.gpu-daemon/`.

| You want | Read |
|---|---|
| To install this on a GPU machine | [`docs/provider.md`](../docs/provider.md) — one command, no clone, no sudo |
| How it is built, and its backend contract | [`docs/reference/daemon.md`](../docs/reference/daemon.md) |
| Every flag and environment variable | [`docs/reference/configuration.md`](../docs/reference/configuration.md#daemon-daemon) |
| To develop against it without a GPU or a backend | [`docs/develop.md`](../docs/develop.md) — mock mode |


Quick sanity run against a local backend:

```bash
cd daemon
pip install -r requirements.txt
python -m daemon.main --backend-url http://localhost:8000 --api-key gk-...
```

The daemon exits at startup if no API key is configured. Keys are created in the
dashboard under **Provider portal → Worker keys**.

### Running tests

Install the daemon with its dev/test dependencies:

```bash
cd daemon
pip install -e ".[dev]"
```

Then run the suite:

```bash
cd daemon
pytest
```


