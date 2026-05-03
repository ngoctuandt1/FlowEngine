"""HTTP client for FlowEngine server API.

Handles claim, update, and heartbeat with retry logic and
connection error handling.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0
REQUEST_TIMEOUT_SEC = 30.0


class RemoteAPI:
    """Async HTTP client for the FlowEngine server."""

    def __init__(self, server_url: str, worker_id: str, api_key: str = ""):
        self.server_url = server_url.rstrip("/")
        self.worker_id = worker_id
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init the httpx client."""
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.server_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SEC,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Worker endpoints
    # ------------------------------------------------------------------

    async def claim_job(self, profiles: list[str]) -> Optional[dict]:
        """POST /api/worker/claim -- claim the next available job.

        Returns the job dict on success, or None if the server
        has no work (HTTP 204).
        """
        payload = {
            "worker_id": self.worker_id,
            "profiles": profiles,
        }
        resp = await self._request("POST", "/api/worker/claim", json=payload)
        if resp is None:
            return None
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    async def claim_job_by_id(
        self,
        job_id: str,
        *,
        profile: Optional[str] = None,
    ) -> Optional[dict]:
        """POST /api/worker/claim-by-id — claim a specific pending job.

        Returns the Job dict on success, None if the job is no longer
        pending (someone else claimed, or cancelled).
        """
        payload = {
            "worker_id": self.worker_id,
            "job_id": job_id,
        }
        if profile is not None:
            payload["profile"] = profile
        resp = await self._request("POST", "/api/worker/claim-by-id", json=payload)
        if resp is None or resp.status_code == 204:
            return None
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.warning("claim_job_by_id non-2xx for %s: %s",
                           job_id, resp.status_code)
            return None
        return resp.json()

    async def update_job(self, job_id: str, update: dict) -> dict:
        """PUT /api/worker/jobs/{job_id} -- push result back to server."""
        resp = await self._request(
            "PUT", f"/api/worker/jobs/{job_id}", json=update
        )
        if resp is None:
            raise ConnectionError(
                f"Failed to update job {job_id} after {MAX_RETRIES} retries"
            )
        resp.raise_for_status()
        return resp.json()

    async def list_pending_l1_siblings(
        self,
        *,
        project_url: Optional[str] = None,
        profile: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """GET /api/jobs/l1-siblings — peek pending L1 siblings for batch.

        Returns a list (possibly empty). Returns [] on transport failure
        rather than raising — batch is opportunistic, single-job claim
        path is the safe fallback.
        """
        params: dict[str, object] = {"limit": limit}
        if project_url is not None:
            params["project_url"] = project_url
        if profile is not None:
            params["profile"] = profile
        resp = await self._request("GET", "/api/jobs/l1-siblings", params=params)
        if resp is None:
            return []
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.warning("list_pending_l1_siblings non-2xx: %s", resp.status_code)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []

    async def list_pending_l2_siblings(
        self,
        *,
        parent_job_id: str,
        profile: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """GET /api/jobs/l2-siblings — peek pending L2 siblings for batch.

        Returns [] on transport failure rather than raising.
        """
        if not parent_job_id:
            return []
        params: dict[str, object] = {
            "parent_job_id": parent_job_id, "limit": limit,
        }
        if profile is not None:
            params["profile"] = profile
        resp = await self._request("GET", "/api/jobs/l2-siblings", params=params)
        if resp is None:
            return []
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.warning("list_pending_l2_siblings non-2xx: %s", resp.status_code)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []

    async def heartbeat(self) -> None:
        """POST /api/worker/heartbeat -- worker keepalive."""
        payload = {"worker_id": self.worker_id}
        resp = await self._request("POST", "/api/worker/heartbeat", json=payload)
        if resp is not None:
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Internal HTTP helper with retries
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, **kwargs
    ) -> Optional[httpx.Response]:
        """Issue an HTTP request with automatic retries on transient errors.

        Returns the Response on success, or None if all retries are exhausted
        due to connection failures.
        """
        import asyncio

        client = await self._get_client()
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.request(method, path, **kwargs)
                return resp
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                logger.warning(
                    "Server unreachable (%s %s), attempt %d/%d: %s",
                    method, path, attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_SEC * attempt)
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "Request timeout (%s %s), attempt %d/%d: %s",
                    method, path, attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_SEC * attempt)

        logger.error(
            "All %d retries exhausted for %s %s: %s",
            MAX_RETRIES, method, path, last_exc,
        )
        return None
