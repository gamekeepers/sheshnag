"""
Mock Backend Server — for testing the daemon WITHOUT the real backend.

This is a minimal FastAPI server that implements the same API contract
that @nirav3690 is building. Use it to test the daemon end-to-end
on your local machine.

Usage:
    pip install fastapi uvicorn python-multipart
    cd daemon
    python -m tests.mock_backend

    # In another terminal:
    python -m daemon.main --config config.yaml

The mock server:
    1. Serves a pre-loaded JSONL file as a job
    2. Assigns it to the first worker that polls
    3. Accepts the output upload
    4. Prints a summary

This lets you verify the full daemon flow without any other infrastructure.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

# ── Configuration ────────────────────────────────────────────

SAMPLE_INPUT = Path(__file__).parent / "sample_input.jsonl"
STORAGE_DIR = Path(__file__).parent / "mock_storage"

app = FastAPI(title="Mock Control Plane", version="0.1.0")

# ── In-memory state ──────────────────────────────────────────

_jobs: dict = {}
_outputs: dict = {}
_workers: dict = {}


def _seed_job() -> None:
    """Create a single test job from the sample input file."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"

    # Copy input file to storage
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    input_dest = STORAGE_DIR / f"{job_id}_input.jsonl"
    shutil.copy(SAMPLE_INPUT, input_dest)

    _jobs[job_id] = {
        "job_id": job_id,
        "model": "mistralai/Mistral-7B-Instruct-v0.2",
        "status": "queued",
        "max_tokens": 512,
        "temperature": 0.7,
        "input_path": str(input_dest),
        "output_path": None,
        "created_at": datetime.utcnow().isoformat(),
        "worker_id": None,
    }
    print(f"\n✅ Seeded test job: {job_id}")
    print(f"   Input: {input_dest}")
    print(f"   Prompts: {sum(1 for _ in open(input_dest))}")


# ── Auth Middleware (Optional — warn if missing) ────────────


@app.middleware("http")
async def check_auth_header(request: Request, call_next):
    """
    Optional auth check — warns if Authorization header is missing.

    Does NOT block requests (this is a test server), but logs a
    warning so you can verify the daemon is sending auth headers.
    """
    if "authorization" not in request.headers:
        if request.url.path not in ("/docs", "/openapi.json", "/"):
            print(f"⚠️  No Authorization header on {request.method} {request.url.path}")
    else:
        auth = request.headers["authorization"]
        print(f"🔑 Auth header present on {request.method} {request.url.path}: {auth[:20]}...")

    return await call_next(request)


# ── Worker APIs ──────────────────────────────────────────────


@app.post("/workers/register")
async def register_worker(body: dict):
    """
    Register a worker with the control plane.

    Stores worker metadata (GPU info, models, runtime) so the
    scheduler knows what this worker can handle (spec §8).
    """
    worker_id = body.get("worker_id", "unknown")
    _workers[worker_id] = body
    print(
        f"\n✅ Worker registered: {worker_id}"
        f" (GPU: {body.get('gpu_name', '?')},"
        f" VRAM: {body.get('vram_gb', '?')}GB,"
        f" Models: {body.get('models', [])},"
        f" Runtime: {body.get('runtime', '?')})"
    )
    return JSONResponse(content={"status": "registered", "worker_id": worker_id})


@app.post("/workers/poll")
async def poll_job(body: dict):
    """
    Worker polls for a job.
    Returns the first queued job, or 204 if none available.
    """
    worker_id = body.get("worker_id", "unknown")

    for job_id, job in _jobs.items():
        if job["status"] == "queued":
            job["status"] = "running"
            job["worker_id"] = worker_id
            print(f"\n📋 Job {job_id} assigned to worker {worker_id}")
            return JSONResponse(
                content={
                    "job": {
                        "job_id": job["job_id"],
                        "model": job["model"],
                        "status": "running",
                        "max_tokens": job["max_tokens"],
                        "temperature": job["temperature"],
                    }
                }
            )

    return Response(status_code=204)


@app.get("/jobs/{job_id}/input")
async def download_input(job_id: str):
    """Serve the input JSONL file for a job."""
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    input_path = _jobs[job_id]["input_path"]
    if not Path(input_path).exists():
        return JSONResponse(status_code=404, content={"error": "Input file missing"})

    print(f"\n📥 Worker downloading input for job {job_id}")
    return FileResponse(input_path, media_type="application/jsonl")


@app.post("/workers/upload-results")
async def upload_results(
    job_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Accept output JSONL upload from worker."""
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    # Save output file
    output_dest = STORAGE_DIR / f"{job_id}_output.jsonl"
    content = await file.read()
    with open(output_dest, "wb") as fh:
        fh.write(content)

    _jobs[job_id]["status"] = "completed"
    _jobs[job_id]["output_path"] = str(output_dest)

    # Print summary
    print(f"\n{'='*60}")
    print(f"✅ JOB {job_id} COMPLETED!")
    print(f"{'='*60}")
    print(f"   Output: {output_dest}")
    print(f"   Size: {len(content):,} bytes")

    # Parse and display results
    try:
        lines = content.decode().strip().split("\n")
        for line in lines:
            result = json.loads(line)
            cid = result.get("custom_id", "?")
            error = result.get("error")
            if error:
                print(f"   ❌ {cid}: {error}")
            else:
                resp = result.get("response", {})
                choices = resp.get("choices", [])
                if choices:
                    text = choices[0].get("message", {}).get("content", "")[:80]
                    print(f"   ✅ {cid}: {text}...")
                else:
                    print(f"   ✅ {cid}: (response received)")
    except Exception as e:
        print(f"   (Could not parse output: {e})")

    print(f"{'='*60}\n")

    return JSONResponse(content={"status": "ok"})


@app.post("/jobs/{job_id}/fail")
async def report_failure(job_id: str, body: dict):
    """
    Worker reports a job failure.

    Updates the job status to "failed" and stores the error message
    so the backend can decide whether to requeue or notify the user.
    """
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    error_msg = body.get("error", "unknown")
    worker_id = body.get("worker_id", "unknown")
    _jobs[job_id]["status"] = "failed"
    _jobs[job_id]["error"] = error_msg

    print(f"\n❌ Job {job_id} FAILED (worker: {worker_id})")
    print(f"   Error: {error_msg[:200]}")

    return JSONResponse(content={"status": "ok"})


# ── User APIs (for completeness) ────────────────────────────


@app.get("/jobs")
async def list_jobs():
    """List all jobs."""
    return JSONResponse(content={"jobs": list(_jobs.values())})


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Get job status."""
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return JSONResponse(content=_jobs[job_id])


@app.get("/jobs/{job_id}/outputs")
async def download_outputs(job_id: str):
    """Download the output file."""
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    output_path = _jobs[job_id].get("output_path")
    if not output_path or not Path(output_path).exists():
        return JSONResponse(status_code=404, content={"error": "Output not ready"})

    return FileResponse(output_path, media_type="application/jsonl")


# ── Startup ──────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Seed a test job on server start."""
    _seed_job()
    print("\n🚀 Mock backend running at http://localhost:8000")
    print("   Waiting for worker to poll...\n")


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
