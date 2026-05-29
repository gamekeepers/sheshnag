"""
HTTP client for communication with the control plane backend.

All backend interactions are encapsulated here so the Worker class
never deals with raw HTTP. This follows Single Responsibility and
makes it trivial to:
    - Mock the client for testing
    - Swap HTTP transport (e.g., gRPC in future)
    - Add auth headers, retries, etc. in one place

API contract assumptions (agreed with @nirav3690):
    POST /workers/register      → registers worker with metadata
    POST /workers/poll           → returns assigned job or 204
    GET  /jobs/{job_id}/input    → downloads input JSONL
    POST /workers/upload-results → multipart upload of output JSONL
    POST /jobs/{job_id}/fail     → reports job failure
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

    async def register_worker(self, worker_info: WorkerInfo) -> None:
        """
        Register this worker with the control plane.

        Per spec §8, a worker must register with metadata (GPU info,
        models, runtime) before it begins polling for jobs. This lets
        the scheduler know what this worker can handle.

        Args:
            worker_info: Worker registration payload.

        Raises:
            httpx.HTTPError: On network or server errors.
        """
        client = self._get_client()

        logger.info(
            f"Registering worker '{worker_info.worker_id}' "
            f"(GPU: {worker_info.gpu_name}, "
            f"VRAM: {worker_info.vram_gb}GB, "
            f"models: {worker_info.models})"
        )

        response = await client.post(
            "/workers/register",
            json=worker_info.model_dump(),
        )
        response.raise_for_status()
        logger.info(f"Worker registered successfully ✓")

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
        logger.info(f"Received job assignment: {job.job_id} (model: {job.model})")
        return job

    # ── Input Download ───────────────────────────────────────────

    async def download_input(self, job_id: str, dest_path: str | Path) -> Path:
        """
        Download the input JSONL file for a job.

        Args:
            job_id:    The job's unique identifier.
            dest_path: Local path where the file should be saved.

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
            f"/jobs/{job_id}/input",
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

    async def upload_results(self, job_id: str, output_path: str | Path) -> None:
        """
        Upload the output JSONL file for a completed job.

        Uses multipart/form-data with the job_id and the output file.

        Args:
            job_id:      The job's unique identifier.
            output_path: Local path to the output JSONL file.

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
                data={"job_id": job_id},
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
        but don't crash. The backend's heartbeat timeout will
        eventually reclaim the job anyway.

        Args:
            job_id: The failed job's identifier.
            error:  Error description (truncated to 2000 chars).
        """
        client = self._get_client()

        try:
            response = await client.post(
                f"/jobs/{job_id}/fail",
                json={
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

    # ── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.debug("Backend client closed")
