"""
HTTP client for communication with the control plane backend.

All backend interactions are encapsulated here so the Worker class
never deals with raw HTTP. This follows Single Responsibility and
makes it trivial to:
    - Mock the client for testing
    - Swap HTTP transport (e.g., gRPC in future)
    - Add auth headers, retries, etc. in one place

API contract (all calls authenticated with an org worker API key):
    POST /workers/register                → registers worker, returns assigned worker_id
    POST /workers/{worker_id}/heartbeat   → liveness + dynamic capability stats
    POST /workers/poll                    → returns assigned batch or {"job": null}
    GET  /v1/files/{id}/content           → downloads input JSONL (path from poll)
    POST /workers/upload-results          → multipart upload of output JSONL + real counts
    POST /workers/report-failure          → reports job failure (backend requeues)
    POST /workers/progress                → live prompt counts every N prompts
    POST /workers/model-progress          → model download progress
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx

from daemon.log import get_logger
from daemon.models import Job, WorkerInfo

logger = get_logger(__name__)

# Poll is a lightweight call — use a shorter timeout to fail fast
# and retry on the next cycle rather than hanging for 30s.
_POLL_TIMEOUT = 10.0

# Default timeout for non-poll requests
_DEFAULT_TIMEOUT = 30.0


class BackendClient:
    """
    HTTP client for the control plane API.

    All methods are async and return parsed models (not raw responses).
    Errors are logged and re-raised for the Worker to handle.

    Features:
        - Connection pooling for long-running daemon processes
        - Automatic retry on transient failures (connection reset, 502, 503)
        - Optional Bearer token authentication (spec §17)

    Args:
        base_url:  Base URL of the FastAPI backend.
        worker_id: This worker's unique identifier (sent in poll requests).
        api_key:   Optional API key for Bearer token authentication.
    """

    def __init__(
        self,
        base_url: str,
        worker_id: str,
        api_key: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._worker_id = worker_id
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    def update_api_key(self, api_key: str) -> None:
        """Update the API key after registration."""
        self._api_key = api_key
        if self._client:
            self._client.headers["Authorization"] = f"Bearer {api_key}"

    def update_worker_id(self, worker_id: str) -> None:
        """Adopt the backend-assigned worker id after registration."""
        self._worker_id = worker_id

    def _get_client(self) -> httpx.AsyncClient:
        """
        Lazy-initialize the HTTP client with production-grade settings.

        Configures:
            - Connection pooling (max 10 connections, 5 keep-alive)
            - Automatic retry on transient transport errors (3 retries)
            - Auth headers if an API key is configured
        """
        if self._client is None or self._client.is_closed:
            headers = {"User-Agent": f"gpu-daemon/{self._worker_id}"}

            # Inject Bearer token if API key is configured (spec §17)
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
                headers=headers,
                # Connection pooling for a long-running daemon process
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
                # Retry on transient transport-level failures
                transport=httpx.AsyncHTTPTransport(retries=3),
            )
        return self._client

    # ── Worker Registration (Spec §8) ────────────────────────────

    async def register_worker(self, worker_info: WorkerInfo) -> dict:
        """
        Register this worker with the control plane.

        Per spec §8, a worker must register with metadata (GPU info,
        models, runtime) before it begins polling for jobs. This lets
        the scheduler know what this worker can handle.

        Registration is authenticated with the org worker API key
        — the key is created in the dashboard and must
        be configured before the daemon starts. The backend derives the
        owning organization from the key and returns the assigned
        worker_id; it never issues API keys.

        Args:
            worker_info: Worker registration payload.

        Returns:
            Dictionary containing 'status', 'worker_id', and 'message'.
        """
        client = self._get_client()

        payload = {
            "hostname": worker_info.hardware.hostname if worker_info.hardware else worker_info.worker_id,
            "os": worker_info.hardware.os if worker_info.hardware else "unknown",
            "cpu": {"cores": worker_info.hardware.cpu_cores} if worker_info.hardware else None,
            "ram": {"total_gb": worker_info.hardware.ram_gb} if worker_info.hardware else None,
            "gpus": [
                {
                    "index": gpu.index,
                    "vendor": "nvidia",
                    "name": gpu.name,
                    "vram_gb": gpu.vram_gb,
                    "driver": gpu.driver_version,
                    "cuda": gpu.cuda_version,
                }
                for gpu in (worker_info.hardware.gpus if worker_info.hardware else [])
            ],
            "runtimes": [
                {
                    "type": worker_info.runtime,
                    "endpoint": "localhost",
                    "models": worker_info.models,
                    "model_digests": worker_info.model_digests,
                }
            ],
        }

        response = await client.post(
            "/workers/register",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        logger.info(f"Worker registered: {worker_info.worker_id}")
        return data

    # ── Job Polling ──────────────────────────────────────────────

    async def poll_job(self) -> Optional[Job]:
        """
        Ask the backend for an available job.

        Uses a shorter timeout than other calls since polling is
        lightweight — if it hangs, we'd rather fail fast and retry
        on the next poll cycle.

        Returns:
            A Job if one was assigned, None if the queue is empty.

        Raises:
            httpx.HTTPError: On network or server errors.
        """
        client = self._get_client()

        response = await client.post(
            "/workers/poll",
            json={"worker_id": self._worker_id},
            timeout=httpx.Timeout(_POLL_TIMEOUT),
        )

        # 204 No Content = no jobs available
        if response.status_code == 204:
            return None

        response.raise_for_status()
        data = response.json()

        # Handle both {"job": {...}} and {"job": null} formats
        job_data = data.get("job")
        if job_data is None:
            return None

        job = Job(**job_data)
        logger.info(
            f"Received job assignment: {job.job_id} "
            f"(input_file_id: {job.input_file_id})"
        )
        return job

    # ── Input Download ───────────────────────────────────────────

    async def download_input(
        self, job_id: str, input_path: str, dest_path: str | Path
    ) -> Path:
        """
        Download the input JSONL file for a job.

        Uses the input_path provided by the poll response (e.g.,
        "/v1/files/{file_id}/content") rather than constructing the
        URL ourselves. This decouples the daemon from the backend's
        file storage layout.

        Args:
            job_id:     The job's unique identifier (for logging).
            input_path: Server-provided URL path to the input file.
            dest_path:  Local path where the file should be saved.

        Returns:
            Path to the downloaded file.

        Raises:
            httpx.HTTPError: On download failure.
        """
        client = self._get_client()
        dest = Path(dest_path)

        logger.debug(f"Downloading input for job {job_id} → {dest}")

        # Stream the download for large files
        async with client.stream(
            "GET",
            input_path,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    fh.write(chunk)

        file_size = dest.stat().st_size
        logger.info(
            f"Downloaded input for job {job_id} "
            f"({file_size:,} bytes → {dest})"
        )
        return dest

    # ── Result Upload ────────────────────────────────────────────

    async def upload_results(
        self,
        job_id: str,
        output_path: str | Path,
        completed: int = 0,
        failed: int = 0,
    ) -> None:
        """
        Upload the output JSONL file for a completed job.

        Uses multipart/form-data with the job_id, this worker's id (the
        backend verifies the batch is assigned to us), the real
        completed/failed counts, and the output file.

        Args:
            job_id:      The job's unique identifier.
            output_path: Local path to the output JSONL file.
            completed:   Number of prompts that succeeded.
            failed:      Number of prompts that failed.

        Raises:
            httpx.HTTPError: On upload failure.
        """
        client = self._get_client()
        output = Path(output_path)

        file_size = output.stat().st_size
        logger.debug(
            f"Uploading results for job {job_id} "
            f"({file_size:,} bytes from {output})"
        )

        with open(output, "rb") as fh:
            response = await client.post(
                "/workers/upload-results",
                data={
                    "job_id": job_id,
                    "worker_id": self._worker_id,
                    "completed": str(completed),
                    "failed": str(failed),
                },
                files={"file": ("output.jsonl", fh, "application/jsonl")},
                timeout=httpx.Timeout(120.0),  # Large file upload timeout
            )

        response.raise_for_status()
        logger.info(f"Uploaded results for job {job_id} ({file_size:,} bytes)")

    # ── Failure Reporting ────────────────────────────────────────

    async def report_failure(self, job_id: str, error: str) -> None:
        """
        Report a job failure to the backend so it can requeue the job.

        This is a best-effort call — if it fails, we log a warning
        but don't crash. The backend requeues the batch (up to a max
        attempt count, spec §12), and its heartbeat sweeper will
        eventually reclaim the job even if this report never arrives.

        Args:
            job_id: The failed job's identifier.
            error:  Error description (truncated to 2000 chars).
        """
        client = self._get_client()

        try:
            response = await client.post(
                "/workers/report-failure",
                json={
                    "job_id": job_id,
                    "worker_id": self._worker_id,
                    "error": error[:2000],  # Prevent oversized payloads
                },
                timeout=httpx.Timeout(_POLL_TIMEOUT),
            )
            response.raise_for_status()
            logger.info(f"Reported failure for job {job_id}")
        except Exception as exc:
            # Best-effort — don't crash if failure reporting itself fails
            logger.warning(f"Failed to report failure for job {job_id}: {exc}")

    # ── Heartbeat and Progress ───────────────────────────────────
    
    async def send_heartbeat(self, payload: dict) -> None:
        """POST /workers/{worker_id}/heartbeat — best-effort, never crash."""
        client = self._get_client()
        try:
            response = await client.post(
                f"/workers/{self._worker_id}/heartbeat",
                json=payload,
                timeout=httpx.Timeout(10.0),
            )
            response.raise_for_status()
            logger.debug(f"Heartbeat sent (activity={payload.get('activity')})")
        except Exception as exc:
            logger.warning(f"Heartbeat failed: {exc}")

    async def report_progress(self, job_id: str, completed: int, failed: int, total: int) -> None:
        """Report batch processing progress to the platform."""
        client = self._get_client()
        try:
            response = await client.post(
                f"/workers/progress",
                json={
                    "job_id": job_id,
                    "worker_id": self._worker_id,
                    "completed": completed,
                    "failed": failed,
                    "total": total,
                },
                timeout=httpx.Timeout(10.0),
            )
            response.raise_for_status()
        except Exception as exc:
            logger.debug(f"Progress report failed (non-fatal): {exc}")

    async def report_model_download(self, worker_id: str, model_name: str, status: str, completed: int, total: int) -> None:
        """Report model download progress to platform."""
        client = self._get_client()
        try:
            response = await client.post(
                f"/workers/model-progress",
                json={
                    "worker_id": worker_id,
                    "model_name": model_name,
                    "status": status,
                    "completed": completed,
                    "total": total,
                },
                timeout=httpx.Timeout(10.0),
            )
            response.raise_for_status()
        except Exception as exc:
            logger.debug(f"Model progress report failed (non-fatal): {exc}")

    # ── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.debug("Backend client closed")
