# forge-runner

Turns a Sheshnag finetune job into a trained LoRA adapter. It polls
`/workers/finetune-poll` and writes a LLaMA-Factory config for the job. It then
runs the pinned engine image as a supervised subprocess, streams
`trainer_log.jsonl` back as progress events, and uploads the adapter.

It is deliberately **not** part of the batch daemon (`daemon/`). A six-hour
training run is not a batch of prompts. The backend never restarts a run from
step 0 behind the user's back: a lost runner fails its job with a reason, and
the user resubmits. The contract it implements is
[`docs/finetune_contract.md`](../docs/finetune_contract.md).

## Run it

```bash
python3 -m venv ~/venvs/forge-runner
~/venvs/forge-runner/bin/pip install -e runner/

# Against Sheshnag (worker gk- key from the dashboard):
FORGE_API_KEY=gk-... forge-runner serve \
    --backend-url https://sheshnag.dau.ac.in \
    --work-dir /srv/forge/runs/$USER/jobs --hf-cache /srv/forge/hf-cache

# One job, no backend: develop locally, run here
forge-runner run-local --job job.json --train train.jsonl [--validation test.jsonl] \
    --work-dir /srv/forge/runs/$USER/jobs --hf-cache /srv/forge/hf-cache
```

`job.json` takes the `/workers/finetune-poll` payload shape: `job_id`,
`source_ref`, `source_revision`, `lora_rank`, `epochs`, and so on.

`serve` claims a job only when the GPU has at least `--min-free-vram-gb` free
(default 4.5). Watchtower is shared, and a job claimed into an OOM wastes an
attempt.

`--engine-cmd "llamafactory-cli train {config}"` runs a locally installed
engine instead of the docker image.

## What it does with a job

1. Downloads the training (and validation) file into `<work-dir>/<job_id>/data/`.
2. Writes `train.yaml` and `dataset_info.json`. The keys are the G2 run's
   (`forge_runner/config.py`). Bases with no chat template configured are
   refused rather than guessed.
3. Runs the pinned LLaMA-Factory image (`docker run --rm --name forge-<job_id>`)
   with the job directory mounted at the same path inside the container.
4. Every 2 s it forwards new `trainer_log.jsonl` lines to
   `/workers/finetune-progress`, and heartbeats every 30 s. If the backend
   answers `stop: true`, it runs `docker stop`.
5. On exit 0, it zips `adapter_config.json` and `adapter_model.safetensors`
   (made 0644) and uploads them with the engine pin and `all_results.json`
   metrics.
6. Otherwise it classifies the engine log (`forge_runner/errors.py`) into a
   `code`, a message the user can act on, and `retryable`.

| code | retryable | typical cause |
|---|---|---|
| `gpu_out_of_memory` | yes | shared GPU had too little free VRAM |
| `runner_config_rejected` | no | the engine rejected a generated key (a runner bug) |
| `no_trainable_examples` | no | nothing survived the chat template / cutoff |
| `base_revision_missing`, `base_model_unavailable` | no | catalogue pin or HF access |
| `network_error`, `worker_disk_full`, `engine_not_started`, `engine_killed` | yes | the worker, not the job |
| `training_failed` | yes | anything else; the message quotes the engine's last error line |

## Tests

```bash
cd runner && pip install -e ".[dev]" && python -m pytest tests/ -q
```

`tests/fake_engine.py` stands in for `llamafactory-cli`. It writes a real
`trainer_log.jsonl` and adapter files, or fails the way the engine does, so
the supervisor is tested end to end without a GPU.
