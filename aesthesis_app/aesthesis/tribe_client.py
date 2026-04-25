"""HTTP client for the TRIBE service.

Two transport options:
- Multipart: client uploads the MP4 directly. Used when the app and the
  TRIBE service are deployed separately (the typical case — Modal).
- JSON path: client passes a filesystem path that's already on the worker.
  Used when both services share a machine and a volume.

The orchestrator calls the multipart variant per DESIGN.md §10 Q11
("multipart upload to Aesthesis app server, then forward to Modal-hosted
TRIBE service via multipart").

Failures from the service are translated into ``TribeServiceError`` with
the HTTP status code attached so the API layer can map them to a 400/500.
The error message includes the response body so a Modal proxy timeout
(empty body or HTML 504) doesn't surface as an opaque ``JSONDecodeError``.

While the request is in flight, an asyncio heartbeat task logs every
``HEARTBEAT_SECONDS`` so it's obvious whether the call is making progress
or has truly hung. TRIBE pipelines take 6-8s warm and >2 minutes cold
(model load + whisperx transcription).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


HEARTBEAT_SECONDS: float = 10.0


class TribeServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


async def _heartbeat(label: str, t0: float, run_id: str) -> None:
    """Background asyncio task: emit one log line every HEARTBEAT_SECONDS so
    the operator can tell the request is alive (vs the 'silent 99%' problem)."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            elapsed = time.perf_counter() - t0
            log.info(
                "%s still in flight — %.1fs elapsed",
                label, elapsed,
                extra={"step": "tribe_client.heartbeat", "run_id": run_id,
                       "elapsed_s": round(elapsed, 1)},
            )
    except asyncio.CancelledError:
        # Normal path: caller cancels us when the await returns.
        return


def _truncate(s: str, n: int = 800) -> str:
    return s if len(s) <= n else s[:n] + f" […+{len(s)-n} more chars]"


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
        """POST the video as multipart to ``/process_video_timeline``.

        Async because the orchestrator runs both videos serially via
        ``await`` — the awaitable nature also lets the FastAPI event loop
        cooperate while we wait for the GPU.

        Logs the response status, headers, and body shape on every return,
        so non-JSON / proxy-timeout / cold-start failures surface a useful
        diagnostic instead of an opaque ``JSONDecodeError`` deep in httpx.
        """
        if not video_path.exists():
            raise TribeServiceError(f"video not found: {video_path}", status_code=400)

        size_bytes = video_path.stat().st_size
        log.info(
            "TRIBE call begin",
            extra={"step": "tribe_client", "run_id": run_id,
                   "url": self.base_url, "video": str(video_path),
                   "video_size_bytes": size_bytes,
                   "timeout_s": self.timeout_s,
                   "heartbeat_s": HEARTBEAT_SECONDS},
        )

        timeout = httpx.Timeout(self.timeout_s, connect=10.0)
        t0 = time.perf_counter()

        # Spin up the heartbeat in the background so the operator gets
        # progress lines even while we're stuck inside the await.
        heartbeat_task = asyncio.create_task(
            _heartbeat("TRIBE call", t0, run_id),
            name=f"tribe-heartbeat-{run_id}",
        )

        try:
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
                    except httpx.ReadTimeout as e:
                        elapsed = time.perf_counter() - t0
                        log.error(
                            "TRIBE call read-timeout",
                            extra={"step": "tribe_client", "run_id": run_id,
                                   "elapsed_s": round(elapsed, 1)},
                        )
                        raise TribeServiceError(
                            f"TRIBE read timeout after {elapsed:.0f}s "
                            f"(timeout_s={self.timeout_s}). The Modal proxy "
                            f"may have given up before the GPU container "
                            f"finished. First-call cold starts can take "
                            f"several minutes.",
                            status_code=504,
                        ) from e
                    except httpx.HTTPError as e:
                        elapsed = time.perf_counter() - t0
                        log.error(
                            "TRIBE call transport error: %s",
                            e,
                            extra={"step": "tribe_client", "run_id": run_id,
                                   "elapsed_s": round(elapsed, 1),
                                   "error_type": type(e).__name__},
                        )
                        raise TribeServiceError(
                            f"TRIBE transport error after {elapsed:.0f}s: "
                            f"{type(e).__name__}: {e}",
                            status_code=502,
                        ) from e
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        elapsed = time.perf_counter() - t0
        content_type = r.headers.get("content-type", "")
        body_len = len(r.content)
        log.info(
            "TRIBE response received",
            extra={"step": "tribe_client", "run_id": run_id,
                   "status_code": r.status_code,
                   "content_type": content_type,
                   "body_bytes": body_len,
                   "elapsed_s": round(elapsed, 1)},
        )

        if r.status_code >= 400:
            # Try JSON first (FastAPI errors return JSON); fall back to
            # text so HTML 504 / empty body still produces a useful message.
            try:
                body: Any = r.json()
                body_repr = body
            except ValueError:
                body = r.text
                body_repr = _truncate(body)
            log.error(
                "TRIBE returned non-2xx",
                extra={"step": "tribe_client", "run_id": run_id,
                       "status_code": r.status_code, "body": body_repr},
            )
            raise TribeServiceError(
                f"TRIBE returned {r.status_code} after {elapsed:.0f}s. "
                f"body={body_repr}",
                status_code=r.status_code,
                body=body,
            )

        # Status was 2xx but body might still not be JSON if a proxy
        # mangled it. Surface the actual content with a useful error.
        if "json" not in content_type.lower():
            text = _truncate(r.text)
            log.error(
                "TRIBE returned 2xx but non-JSON body",
                extra={"step": "tribe_client", "run_id": run_id,
                       "content_type": content_type, "body": text,
                       "body_bytes": body_len},
            )
            raise TribeServiceError(
                f"TRIBE returned {r.status_code} with content-type "
                f"{content_type!r} (body_bytes={body_len}). "
                f"Modal's HTTP proxy has a ~150s sync-request timeout — "
                f"if the GPU pipeline takes longer the proxy returns a "
                f"non-JSON timeout page even though the container is still "
                f"running. body={text}",
                status_code=502,
            )

        try:
            payload = r.json()
        except ValueError as e:
            text = _truncate(r.text)
            log.error(
                "TRIBE JSON decode failed",
                extra={"step": "tribe_client", "run_id": run_id,
                       "content_type": content_type, "body": text},
            )
            raise TribeServiceError(
                f"TRIBE returned a body content-type {content_type!r} but "
                f"json.loads failed: {e}. body={text}",
                status_code=502,
            ) from e

        log.info(
            "TRIBE call done",
            extra={"step": "tribe_client", "run_id": run_id,
                   "n_frames": len(payload.get("frames", [])),
                   "n_windows": len(payload.get("windows", [])),
                   "tribe_processing_ms": payload.get("processing_time_ms"),
                   "elapsed_s": round(elapsed, 1)},
        )
        return payload
