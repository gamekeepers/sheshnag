"""
HTTP client for communication with the control plane backend.

All backend interactions are encapsulated here so the Worker class
never deals with raw HTTP. This follows Single Responsibility and
makes it trivial to:
    - Mock the client for testing
    - Swap HTTP transport (e.g., gRPC in future)
    - Add auth headers, retries, etc. in one place

API contract assumptions (agreed with @nirav3690):
    POST /workers/poll          → returns assigned job or 204
    GET  /jobs/{job_id}/input   → downloads input JSONL
    POST /workers/upload-results → multipart upload of output JSONL
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx

from daemon.log import get_logger
from daemon.models import Job, PollResponse

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0


class BackendClient:
    """
    HTTP client for the control plane API.

    All methods are async and return parsed models (not raw responses).
    Errors are logged and re-raised for the Worker to handle.

    Args:
        base_url:  Base URL of the FastAPI backend.
        worker_id: This worker's unique identifier (sent in poll requests).
    """

    def __init__(self, base_url: str, worker_id: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._worker_id = worker_id
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
                headers={"User-Agent": f"gpu-daemon/{self._worker_id}"},
            )
        return self._client

    # ── Job Polling ──────────────────────────────────────────────

    async def poll_job(self) -> Optional[Job]:
        """
        Ask the backend for an available job.

        Returns:
            A Job if one was assigned, None if the queue is empty.

        Raises:
            httpx.HTTPError: On network or server errors.
        """
        client = self._get_client()

        response = await client.post(
            "/workers/poll",
            json={"worker_id": self._worker_id},
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

    # ── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.debug("Backend client closed")
