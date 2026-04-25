"""HTTP client for the TRIBE service.

Two transport options:
- Multipart: client uploads the MP4 directly. Used when the app and the
  TRIBE service are deployed separately (the typical case — Modal).
- JSON path: client passes a filesystem path that's already on the worker.
  Used in mock-mode local dev when both services share a machine.

The orchestrator calls the multipart variant per DESIGN.md §10 Q11
("multipart upload to Aesthesis app server, then forward to Modal-hosted
TRIBE service via multipart").

Failures from the service are translated into `TribeServiceError` with the
HTTP status code attached so the API layer can map them to a 400/500.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TribeServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TribeClient:
    def __init__(self, base_url: str, *, timeout_s: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def health(self) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            r = await client.get("/health")
            r.raise_for_status()
            return r.json()

    async def process_video_timeline(
        self,
        video_path: Path,
        *,
        window_trs: int = 4,
        step_trs: int = 1,
        run_id: str,
    ) -> dict:
        """POST the video as multipart to /process_video_timeline.

        Async because the orchestrator runs both videos serially via
        `await` — the awaitable nature also lets the FastAPI event loop
        cooperate while we wait for the GPU (3-8s per call).
        """
        if not video_path.exists():
            raise TribeServiceError(f"video not found: {video_path}", status_code=400)

        log.info(
            "TRIBE call begin",
            extra={"step": "tribe_client", "run_id": run_id,
                   "url": self.base_url, "video": str(video_path)},
        )

        timeout = httpx.Timeout(self.timeout_s, connect=10.0)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
            with video_path.open("rb") as fh:
                files = {"video": (video_path.name, fh, "video/mp4")}
                data = {
                    "window_trs": str(window_trs),
                    "step_trs": str(step_trs),
                    "run_id": run_id,
                }
                try:
                    r = await client.post(
                        "/process_video_timeline",
                        files=files,
                        data=data,
                    )
                except httpx.HTTPError as e:
                    raise TribeServiceError(
                        f"TRIBE call failed: {e}",
                        status_code=502,
                    ) from e

        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            raise TribeServiceError(
                f"TRIBE returned {r.status_code}: {body}",
                status_code=r.status_code,
                body=body,
            )

        payload = r.json()
        log.info(
            "TRIBE call done",
            extra={"step": "tribe_client", "run_id": run_id,
                   "n_frames": len(payload.get("frames", [])),
                   "elapsed_ms": payload.get("processing_time_ms")},
        )
        return payload
