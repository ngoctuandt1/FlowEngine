"""Batch-mode project session on one FlowClient."""

import asyncio
import os
from collections.abc import Iterable

from flow.operations.camera import download_camera_move, submit_camera_move
from flow.operations.extend import download_extend_video, submit_extend_video
from flow.operations.insert import download_insert_object, submit_insert_object
from flow.operations.remove import download_remove_object, submit_remove_object

PROFILE_BASE_DIR = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
DOWNLOAD_DIR = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")

SUBMIT_SPACING_SEC = 3

SUBMIT_MAP = {
    "extend-video": submit_extend_video,
    "insert-object": submit_insert_object,
    "remove-object": submit_remove_object,
    "camera-move": submit_camera_move,
}

DOWNLOAD_MAP = {
    "extend-video": download_extend_video,
    "insert-object": download_insert_object,
    "remove-object": download_remove_object,
    "camera-move": download_camera_move,
}


class ProjectSession:
    """Run multiple L2 operations on one project with one FlowClient."""

    def __init__(self, profile: str, project_url: str):
        self.profile = profile
        self.project_url = project_url
        self.client = None
        self._client_cm = None
        self.submit_errors: dict[str, Exception] = {}
        self.download_errors: dict[str, Exception] = {}

    async def __aenter__(self):
        from flow.client import FlowClient

        self._client_cm = FlowClient(
            profile_name=self.profile,
            profile_base_dir=PROFILE_BASE_DIR,
            download_dir=DOWNLOAD_DIR,
        )
        self.client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client_cm is None:
            return None
        return await self._client_cm.__aexit__(exc_type, exc, tb)

    async def submit_many(self, jobs: Iterable[dict]) -> list[tuple[dict, dict]]:
        jobs = list(jobs)
        submitted: list[tuple[dict, dict]] = []
        self.submit_errors = {}

        for idx, job in enumerate(jobs):
            try:
                submit_fn = self._submit_for(job["type"])
                ctx = await submit_fn(self.client, job, **self._submit_kwargs(job))
                submitted.append((job, ctx))
            except ValueError:
                raise
            except Exception as exc:
                self.submit_errors[job["id"]] = exc
            if idx < len(jobs) - 1:
                await asyncio.sleep(SUBMIT_SPACING_SEC)

        return submitted

    async def download_all(
        self, submitted_jobs: Iterable[tuple[dict, dict]]
    ) -> list[tuple[dict, dict]]:
        results: list[tuple[dict, dict]] = []
        self.download_errors = {}

        for job, ctx in submitted_jobs:
            try:
                download_fn = self._download_for(job["type"])
                result = await download_fn(self.client, job, ctx)
                results.append((job, result))
            except ValueError:
                raise
            except Exception as exc:
                self.download_errors[job["id"]] = exc

        return results

    def _submit_for(self, job_type: str):
        try:
            return SUBMIT_MAP[job_type]
        except KeyError as exc:
            raise ValueError(f"Unknown job type: {job_type}") from exc

    def _download_for(self, job_type: str):
        try:
            return DOWNLOAD_MAP[job_type]
        except KeyError as exc:
            raise ValueError(f"Unknown job type: {job_type}") from exc

    @staticmethod
    def _submit_kwargs(job: dict) -> dict:
        job_type = job["type"]
        if job_type == "extend-video":
            return {
                "prompt": job.get("prompt", ""),
                "model": job.get("model", "veo-3.1-fast-lp"),
                "free_mode": True,
            }
        if job_type == "insert-object":
            return {
                "prompt": job.get("prompt", ""),
                "bbox": job.get("bbox"),
            }
        if job_type == "remove-object":
            return {"bbox": job.get("bbox")}
        if job_type == "camera-move":
            return {"direction": job.get("direction", "Dolly in")}
        raise ValueError(f"Unknown job type: {job_type}")
