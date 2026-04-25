"""FastAPI surface for the TRIBE service.

Endpoints (see DESIGN.md §5.11 + §5.15.3 for contracts):

    GET  /health                    — liveness + GPU presence
    POST /process_video_timeline    — sync inference; multipart upload OR JSON path
    POST /enqueue_video_timeline    — async via ARQ; returns {job_id}
    GET  /job/{job_id}              — poll ARQ job status

The sync endpoint is the load-bearing one for v1 demos — Modal's
`keep_warm=1` keeps the worker hot, and a 30s clip yields a 3-8s wall time.

The multipart upload path writes the file to a tmp directory and forwards
the local path into `process_video_timeline` (which is filesystem-based per
the underlying tribev2 API).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Path as PathParam, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .constants import STEP_TRS_DEFAULT, WINDOW_TRS_DEFAULT
from .init_resources import Resources, load_resources
from .logging_config import configure_logging, get_logger
from .pipeline import process_video_timeline
from .validation import PipelineError, ValidationError
from . import worker as worker_mod

configure_logging()
log = get_logger(__name__)


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class VideoTimelineRequest(BaseModel):
    """JSON-mode request — caller already has the MP4 on the worker's
    filesystem (used by Modal volume mounts and ARQ enqueue calls)."""
    video_path: str = Field(..., description="Local filesystem path to MP4.")
    window_trs: int = Field(WINDOW_TRS_DEFAULT, ge=2, le=64)
    step_trs: int = Field(STEP_TRS_DEFAULT, ge=1, le=64)
    run_id: str | None = Field(None, description="Trace ID propagated from caller.")


class HealthResponse(BaseModel):
    status: str
    mock_mode: bool
    gpu_available: bool
    arq_available: bool
    n_masks: int
    n_weight_maps: int


class JobAccepted(BaseModel):
    job_id: str
    status: str = "queued"


# ─── App + lifecycle ─────────────────────────────────────────────────────────

app = FastAPI(
    title="Aesthesis TRIBE service",
    version="0.1.0.0",
    description=(
        "Wraps Meta TRIBE v2 as an HTTP API. Takes an MP4, returns a per-TR "
        "brain timeline of 8 UX-tuned ROIs + sliding-window composites."
    ),
)

_resources: Resources | None = None


def _get_resources() -> Resources:
    global _resources
    if _resources is None:
        log.info("first request — loading resources synchronously")
        _resources = load_resources()
    return _resources


@app.on_event("startup")
async def _startup() -> None:
    # Eagerly load — the first request waits anyway, and we want errors
    # to surface at boot time rather than mid-request.
    _get_resources()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _gpu_available() -> bool:
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _save_upload(file: UploadFile, run_id: str) -> Path:
    """Write an UploadFile to a per-request tmp directory. Returns the
    local path."""
    upload_root = Path(os.getenv("TRIBE_UPLOAD_DIR", tempfile.gettempdir())) / "tribe_uploads"
    target_dir = upload_root / run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    out_path = target_dir / f"upload{suffix}"
    with out_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    log.debug(
        "saved upload",
        extra={"step": "upload", "run_id": run_id, "size_bytes": out_path.stat().st_size,
               "path": str(out_path)},
    )
    return out_path


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    res = _get_resources()
    return HealthResponse(
        status="ok",
        mock_mode=res.mock_mode,
        gpu_available=_gpu_available(),
        arq_available=worker_mod.HAS_ARQ,
        n_masks=len(res.masks),
        n_weight_maps=len(res.weight_maps),
    )


@app.post("/process_video_timeline")
async def process_video_timeline_endpoint(
    # Multipart upload: client posts the MP4 directly.
    video: UploadFile | None = File(None),
    window_trs: int = Form(WINDOW_TRS_DEFAULT),
    step_trs: int = Form(STEP_TRS_DEFAULT),
    run_id: str | None = Form(None),
    # JSON: client passes a path that is already on the worker's filesystem
    # (Modal volume mount / shared NFS / etc.). Either form works; multipart
    # wins if both are sent.
    json_request: VideoTimelineRequest | None = None,
) -> JSONResponse:
    res = _get_resources()
    rid = run_id or (json_request.run_id if json_request else None) or str(uuid.uuid4())
    log_extra = {"run_id": rid, "endpoint": "process_video_timeline"}

    if video is not None and video.filename:
        path = _save_upload(video, rid)
    elif json_request is not None and json_request.video_path:
        path = Path(json_request.video_path)
        window_trs = json_request.window_trs
        step_trs = json_request.step_trs
    else:
        raise HTTPException(
            status_code=400,
            detail="Send either multipart `video` or JSON {video_path,...}.",
        )

    log.info(
        "request received",
        extra={**log_extra, "video": str(path),
               "window_trs": window_trs, "step_trs": step_trs},
    )
    t0 = time.perf_counter()
    try:
        payload = process_video_timeline(
            path, res,
            window_trs=window_trs, step_trs=step_trs, run_id=rid,
        )
    except ValidationError as e:
        log.warning("validation error: %s", e, extra=log_extra)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PipelineError as e:
        log.error("pipeline error: %s", e, extra=log_extra)
        raise HTTPException(status_code=500, detail=str(e)) from e

    payload["run_id"] = rid
    log.info(
        "request done",
        extra={**log_extra, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
               "n_frames": len(payload["frames"])},
    )
    return JSONResponse(payload)


@app.post("/enqueue_video_timeline", response_model=JobAccepted, status_code=202)
async def enqueue_video_timeline(req: VideoTimelineRequest) -> JobAccepted:
    if not worker_mod.HAS_ARQ:
        raise HTTPException(
            status_code=501,
            detail="ARQ not installed; use synchronous /process_video_timeline instead.",
        )
    try:
        from arq import create_pool  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=501, detail="arq missing") from e
    pool = await create_pool(worker_mod.WorkerSettings.redis_settings)  # type: ignore[attr-defined]
    job = await pool.enqueue_job(
        "process_video_timeline_task",
        video_path=req.video_path,
        window_trs=req.window_trs,
        step_trs=req.step_trs,
        run_id=req.run_id,
    )
    if job is None:
        raise HTTPException(status_code=500, detail="failed to enqueue job")
    log.info("enqueued job", extra={"run_id": req.run_id, "job_id": job.job_id})
    return JobAccepted(job_id=job.job_id)


@app.get("/job/{job_id}")
async def get_job(job_id: str = PathParam(...)) -> dict[str, Any]:
    if not worker_mod.HAS_ARQ:
        raise HTTPException(status_code=501, detail="ARQ not installed")
    try:
        from arq import create_pool  # type: ignore
        from arq.jobs import Job, JobStatus  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=501, detail="arq missing") from e
    pool = await create_pool(worker_mod.WorkerSettings.redis_settings)  # type: ignore[attr-defined]
    j = Job(job_id, pool)
    status = await j.status()
    if status == JobStatus.complete:
        try:
            result = await j.result(timeout=1)
        except Exception as e:  # noqa: BLE001
            return {"job_id": job_id, "status": "error", "error": str(e)}
        return {"job_id": job_id, "status": "complete", "result": result}
    return {"job_id": job_id, "status": str(status)}
