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
import subprocess
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


def _strip_audio_track(video_path: Path, run_id: str) -> Path:
    """Produce a new MP4 next to ``video_path`` with the audio track removed.

    Aesthesis is video-only and the audio strip is now the **sole** mechanism
    keeping TRIBE off the audio path — no monkey-patch fallback exists. With
    no audio stream in the input MP4:

    1. ``ExtractAudioFromVideo`` (tribev2's first transform) produces zero
       Audio events because moviepy reports ``video.audio is None``.
    2. tribev2.main then prunes the audio and text extractors with
       "Removing extractor … as there are no corresponding events".
    3. The whisperx subprocess (the original 2:30 / 30-s bottleneck) and
       the Wav2Vec-BERT raw-waveform encoder are never reached.

    Uses ``ffmpeg -an -c:v copy`` so the video stream is *not* re-encoded
    (sub-second on a 30 s clip). On any failure this **raises**
    ``PipelineError`` rather than returning the original — silently
    forwarding audio would re-introduce the whisperx bottleneck and
    contradict the "no inner-workings touch" contract.
    """
    stripped = video_path.with_name(f"{video_path.stem}_noaudio{video_path.suffix}")
    if stripped.exists():
        log.debug("audio-stripped MP4 already at %s — reusing", stripped,
                  extra={"step": "audio_strip", "run_id": run_id})
        return stripped

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-an",          # drop ALL audio streams
        "-c:v", "copy",  # don't re-encode video
        str(stripped),
    ]
    t = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error(
            "audio strip failed (%s) — refusing to forward audio-laden video to TRIBE",
            type(e).__name__,
            extra={"step": "audio_strip", "run_id": run_id, "error": str(e)},
        )
        raise PipelineError(
            f"audio strip failed ({type(e).__name__}: {e}); cannot run "
            "TRIBE on a video that still contains an audio stream"
        ) from e
    if proc.returncode != 0 or not stripped.exists() or stripped.stat().st_size == 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:500] if proc.stderr else ""
        log.error(
            "audio strip failed (rc=%d) — refusing to forward audio-laden video to TRIBE. "
            "stderr=%s",
            proc.returncode, stderr,
            extra={"step": "audio_strip", "run_id": run_id},
        )
        raise PipelineError(
            f"audio strip failed (ffmpeg rc={proc.returncode}); cannot run "
            f"TRIBE on a video that still contains an audio stream. stderr={stderr!r}"
        )
    log.info(
        "audio stripped — video-only MP4 ready for tribev2",
        extra={
            "step": "audio_strip", "run_id": run_id,
            "elapsed_ms": round((time.perf_counter() - t) * 1000.0, 2),
            "original_bytes": video_path.stat().st_size,
            "stripped_bytes": stripped.stat().st_size,
            "input": str(video_path), "output": str(stripped),
        },
    )
    return stripped


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    res = _get_resources()
    return HealthResponse(
        status="ok",
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
        raw_path = _save_upload(video, rid)
    elif json_request is not None and json_request.video_path:
        raw_path = Path(json_request.video_path)
        window_trs = json_request.window_trs
        step_trs = json_request.step_trs
    else:
        raise HTTPException(
            status_code=400,
            detail="Send either multipart `video` or JSON {video_path,...}.",
        )

    # Aesthesis is video-only. Strip the audio track at the request
    # boundary so tribev2's audio extractors (ExtractAudioFromVideo,
    # ExtractWordsFromAudio, Wav2Vec-BERT) receive nothing audio-shaped.
    # See _strip_audio_track docstring + DESIGN.md §17.
    path = _strip_audio_track(raw_path, rid)

    log.info(
        "request received",
        extra={**log_extra, "video": str(path),
               "raw_video": str(raw_path),
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
