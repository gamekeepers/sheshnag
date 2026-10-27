import json
from pathlib import Path

import pytest

from daemon.config import DaemonConfig
from daemon.worker import Worker


class DummyExecutor:
    async def execute(self, *args, **kwargs):
        raise NotImplementedError

    async def health_check(self):
        return True

    async def close(self):
        return None


class DummyClient:
    def __init__(self):
        self.uploaded = None
        self.progress = []

    async def download_input(self, job_id, input_path, dest_path):
        Path(dest_path).write_text(Path(input_path).read_text())

    async def report_finetune_progress(self, **kwargs):
        self.progress.append(kwargs)

    async def upload_finetune_adapter(self, job_id, adapter_path):
        self.uploaded = (job_id, str(adapter_path))


@pytest.fixture
def worker(tmp_path):
    config = DaemonConfig(
        backend_url="http://localhost:8000",
        api_key="gk-test",
        runtime="ollama",
        work_dir=str(tmp_path),
    )
    client = DummyClient()
    return Worker(config=config, client=client, executor=DummyExecutor())


def test_parse_finetune_dataset_accepts_valid_chat_sft(tmp_path, worker):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
        + "\n"
    )

    parsed = worker._parse_finetune_dataset(dataset)

    assert len(parsed) == 1
    assert parsed[0]["messages"][-1]["role"] == "assistant"
    assert parsed[0]["messages"][-1]["content"] == "hello"


def test_parse_finetune_dataset_rejects_nonassistant_target(tmp_path, worker):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "user", "content": "still user"}]})
        + "\n"
    )

    with pytest.raises(ValueError, match="assistant"):
        worker._parse_finetune_dataset(dataset)
