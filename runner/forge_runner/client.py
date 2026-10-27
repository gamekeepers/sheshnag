"""Talking to Sheshnag's /workers/finetune-* endpoints with a worker gk- key.

BackendClient is the real one. OfflineClient implements the same calls
against local files, so `forge-runner run-local` can drive a real engine run
on watchtower with no backend in the loop.
"""
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class BackendClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def close(self) -> None:
        self._http.close()

    def register(self, hostname: str, gpus: list[dict]) -> str:
        res = self._http.post("/workers/register", json={
            "hostname": hostname, "gpus": gpus,
            "runtimes": [{"type": "forge-runner", "endpoint": "local", "models": []}],
        })
        res.raise_for_status()
        return res.json()["worker_id"]

    def heartbeat(self, worker_id: str, *, busy: bool, job_id: Optional[str],
                  vram_total_gb: float, vram_free_gb: float) -> None:
        try:
            self._http.post(f"/workers/{worker_id}/heartbeat", json={
                "activity": "busy" if busy else "idle", "current_job_id": job_id,
                "vram_total_gb": vram_total_gb, "vram_available_gb": vram_free_gb,
            }).raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Heartbeat failed (will retry): %s", exc)

    def poll(self, worker_id: str) -> Optional[dict]:
        res = self._http.post("/workers/finetune-poll", json={"worker_id": worker_id})
        res.raise_for_status()
        return res.json().get("job")

    def download(self, path: str, dest: Path) -> None:
        with self._http.stream("GET", path) as res:
            res.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in res.iter_bytes():
                    fh.write(chunk)

    def progress(self, job_id: str, worker_id: str, **fields) -> bool:
        """Send one metrics step. Returns True if the job should stop."""
        try:
            res = self._http.post("/workers/finetune-progress",
                                  json={"job_id": job_id, "worker_id": worker_id, **fields})
            res.raise_for_status()
            return bool(res.json().get("stop"))
        except httpx.HTTPError as exc:
            logger.warning("Progress report failed (training continues): %s", exc)
            return False

    def upload_adapter(self, job_id: str, worker_id: str, zip_path: Path, *,
                       engine: str, engine_version: Optional[str],
                       engine_image: Optional[str], metrics: dict) -> dict:
        data = {"job_id": job_id, "worker_id": worker_id, "engine": engine,
                "metrics": json.dumps(metrics)}
        if engine_version:
            data["engine_version"] = engine_version
        if engine_image:
            data["engine_image"] = engine_image
        with open(zip_path, "rb") as fh:
            res = self._http.post("/workers/finetune-upload-adapter", data=data,
                                  files={"file": (zip_path.name, fh, "application/zip")},
                                  timeout=300.0)
        res.raise_for_status()
        return res.json()

    def report_failure(self, job_id: str, worker_id: str, *, error: str,
                       code: str, retryable: bool) -> None:
        try:
            self._http.post("/workers/finetune-report-failure", json={
                "job_id": job_id, "worker_id": worker_id, "error": error[:2000],
                "code": code, "retryable": retryable,
            }).raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Could not report failure of %s: %s", job_id, exc)


class OfflineClient:
    """Same calls as BackendClient, against local files (`run-local`)."""

    def __init__(self, files: dict[str, Path], results_dir: Path):
        self.files = files          # dataset_path -> local file
        self.results_dir = results_dir
        self.outcome: Optional[dict] = None

    def download(self, path: str, dest: Path) -> None:
        shutil.copyfile(self.files[path], dest)

    def progress(self, job_id: str, worker_id: str, **fields) -> bool:
        loss = f" loss={fields['loss']:.4f}" if "loss" in fields else ""
        print(f"[{job_id}] step {fields['current_step']}/{fields['total_steps']}{loss}", flush=True)
        return False

    def upload_adapter(self, job_id: str, worker_id: str, zip_path: Path, **meta) -> dict:
        dest = self.results_dir / zip_path.name
        if dest != zip_path:
            shutil.copyfile(zip_path, dest)
        self.outcome = {"status": "succeeded", "adapter": str(dest), **meta}
        return self.outcome

    def report_failure(self, job_id: str, worker_id: str, **failure) -> None:
        self.outcome = {"status": "failed", **failure}
