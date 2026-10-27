"""forge-runner tests: config generation, progress tailing, failure
classification, and the supervisor end to end against a fake engine."""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from forge_runner import config
from forge_runner.engine import CommandEngine
from forge_runner.errors import classify
from forge_runner.job import Job
from forge_runner.progress import TrainerLogTail, to_progress
from forge_runner.supervisor import run_job

FAKE_ENGINE = Path(__file__).parent / "fake_engine.py"
QWEN_SHA = "7ae557604adf67be50417f59c2c2f167def9a775"


def _job(**overrides) -> Job:
    payload = {
        "job_id": "ftjob-test", "source_ref": "Qwen/Qwen2.5-0.5B-Instruct",
        "source_revision": QWEN_SHA, "dataset_path": "/v1/files/file-1/content",
        "lora_rank": 16, "lora_alpha": 32, "target_modules": "all-linear",
        "epochs": 1, "batch_size": 16, "max_seq_length": 1024,
        "some_future_field": "ignored",
    }
    payload.update(overrides)
    return Job.from_payload(payload)


# ─── Config generation ─────────────────────────────────────

def test_config_matches_the_g2_run(tmp_path):
    cfg, info = config.build(_job(), tmp_path, has_validation=False)
    # The settings G2 proved on watchtower.
    assert cfg["template"] == "qwen" and cfg["lora_target"] == "all"
    assert cfg["per_device_train_batch_size"] == 1 and cfg["gradient_accumulation_steps"] == 16
    assert cfg["model_revision"] == QWEN_SHA
    assert cfg["cutoff_len"] == 1024 and cfg["num_train_epochs"] == 1.0
    assert cfg["dataset"] == "train" and "eval_dataset" not in cfg
    assert info["train"]["formatting"] == "sharegpt"


def test_config_with_validation_and_module_list(tmp_path):
    cfg, info = config.build(_job(target_modules="q_proj,v_proj"), tmp_path, has_validation=True)
    assert cfg["lora_target"] == "q_proj,v_proj"
    assert cfg["eval_dataset"] == "validation" and cfg["eval_strategy"] == "epoch"
    assert info["validation"]["file_name"] == "validation.jsonl"


def test_config_refuses_unknown_base(tmp_path):
    with pytest.raises(config.UnsupportedJob, match="no chat template"):
        config.build(_job(source_ref="someone/unknown-7b"), tmp_path, has_validation=False)


def test_config_written_as_yaml(tmp_path):
    path = config.write(_job(), tmp_path, has_validation=False)
    assert yaml.safe_load(path.read_text())["output_dir"] == str(tmp_path / "out")
    assert json.loads((tmp_path / "data" / "dataset_info.json").read_text())["train"]


# ─── Progress ──────────────────────────────────────────────

def test_tail_holds_back_partial_lines(tmp_path):
    log = tmp_path / "trainer_log.jsonl"
    tail = TrainerLogTail(log)
    assert tail.read_new() == []
    log.write_text('{"current_steps": 5, "total_steps": 29, "loss": 2.39, "lr": 0.00017, "epoch": 0.17}\n{"current_st')
    assert [e["current_steps"] for e in tail.read_new()] == [5]
    with open(log, "a") as fh:
        fh.write('eps": 10, "total_steps": 29, "loss": 2.24, "epoch": 0.34}\n')
    assert [e["current_steps"] for e in tail.read_new()] == [10]


def test_to_progress_maps_llamafactory_fields():
    p = to_progress({"current_steps": 5, "total_steps": 29, "loss": 2.39, "lr": 1.7e-4, "epoch": 0.17,
                     "percentage": 17.2, "elapsed_time": "0:00:06"})
    assert p == {"current_step": 5, "total_steps": 29, "current_epoch": 0.17,
                 "loss": 2.39, "learning_rate": 1.7e-4}
    assert to_progress({"eval_loss": 2.3}) is None


# ─── Failure classification ────────────────────────────────

@pytest.mark.parametrize("log, rc, code, retryable", [
    ("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 1.02 GiB", 1, "gpu_out_of_memory", True),
    ("ValueError: Some keys are not used by the HfArgumentParser: ['lora_rnk']", 1, "runner_config_rejected", False),
    ("huggingface_hub.errors.RevisionNotFoundError: Invalid rev id", 1, "base_revision_missing", False),
    ("OSError: [Errno 28] No space left on device", 1, "worker_disk_full", True),
    ("", 137, "engine_killed", True),
    ("step 1\nKeyError: 'messages'\n", 1, "training_failed", True),
])
def test_classify(log, rc, code, retryable):
    failure = classify(log, rc)
    assert (failure.code, failure.retryable) == (code, retryable)
    assert failure.message


def test_classify_quotes_the_last_error_line():
    assert "KeyError: 'messages'" in classify("loading\nKeyError: 'messages'\ncleanup\n", 1).message


# ─── Supervisor end to end (fake engine) ───────────────────

class FakeClient:
    def __init__(self, dataset: Path, stop_after: int | None = None):
        self.dataset = dataset
        self.stop_after = stop_after
        self.progress_calls, self.uploads, self.failures = [], [], []

    def download(self, path, dest):
        dest.write_bytes(self.dataset.read_bytes())

    def progress(self, job_id, worker_id, **fields):
        self.progress_calls.append(fields)
        return self.stop_after is not None and len(self.progress_calls) >= self.stop_after

    def upload_adapter(self, job_id, worker_id, zip_path, **meta):
        self.uploads.append((zip_path, meta))
        return {}

    def report_failure(self, job_id, worker_id, **failure):
        self.failures.append(failure)


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "train.jsonl"
    path.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"},
                                             {"role": "assistant", "content": "hello"}]}) + "\n")
    return path


@pytest.fixture
def engine():
    return CommandEngine([sys.executable, str(FAKE_ENGINE), "{config}"], version="0.9.5")


def test_successful_run_uploads_adapter(tmp_path, dataset, engine, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    client = FakeClient(dataset)
    beats = []
    result = run_job(_job(), client, engine, tmp_path / "jobs", "worker-1",
                     poll_interval=0.05, heartbeat=lambda: beats.append(1))

    assert result.status == "succeeded", result.detail
    assert [p["current_step"] for p in client.progress_calls] == [1, 2, 3, 4]
    assert client.progress_calls[0]["loss"] == pytest.approx(2.4)
    assert beats and not client.failures

    zip_path, meta = client.uploads[0]
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == ["adapter_config.json", "adapter_model.safetensors"]
        assert json.loads(zf.read("adapter_config.json"))["r"] == 16
    assert meta["engine"] == "llamafactory" and meta["engine_version"] == "0.9.5"
    assert meta["metrics"]["train_loss"] == 2.3


def test_oom_is_reported_as_retryable(tmp_path, dataset, engine, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "oom")
    client = FakeClient(dataset)
    result = run_job(_job(), client, engine, tmp_path / "jobs", "worker-1", poll_interval=0.05)
    assert result.status == "failed"
    assert client.failures == [{"error": client.failures[0]["error"],
                                "code": "gpu_out_of_memory", "retryable": True}]
    assert "max_seq_length" in client.failures[0]["error"]
    assert not client.uploads


def test_config_rejection_is_not_retryable(tmp_path, dataset, engine, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "badkey")
    client = FakeClient(dataset)
    run_job(_job(), client, engine, tmp_path / "jobs", "worker-1", poll_interval=0.05)
    assert client.failures[0]["code"] == "runner_config_rejected"
    assert client.failures[0]["retryable"] is False


def test_cancel_stops_the_engine(tmp_path, dataset, engine, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "slow")
    client = FakeClient(dataset, stop_after=1)
    result = run_job(_job(), client, engine, tmp_path / "jobs", "worker-1", poll_interval=0.05)
    assert result.status == "cancelled"
    assert not client.uploads and not client.failures


def test_unsupported_base_fails_before_starting_engine(tmp_path, dataset, monkeypatch):
    class ExplodingEngine:
        def start(self, *a, **k):
            raise AssertionError("engine must not start")
    client = FakeClient(dataset)
    result = run_job(_job(source_ref="someone/unknown-7b"), client, ExplodingEngine(),
                     tmp_path / "jobs", "worker-1")
    assert result.status == "failed"
    assert client.failures[0]["code"] == "unsupported_job"
    assert client.failures[0]["retryable"] is False
