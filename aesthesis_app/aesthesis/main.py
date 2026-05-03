"""FastAPI app — public surface for the Aesthesis backend.

Endpoints:
    GET  /health           — liveness + downstream TRIBE liveness
    POST /api/analyze      — the load-bearing endpoint (single video)

DESIGN.md §10 Q11 / §17: multipart upload of a single MP4, written to a
tmp dir, then forwarded by the orchestrator to the TRIBE service.

Pre-pivot this accepted ``video_a`` + ``video_b`` and returned an A/B
comparison. The pivot to single-video collapsed it — see DESIGN.md §17.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import AppConfig, get_config
from .logging_config import configure_logging, get_logger
from .orchestrator import OrchestratorError, run_analysis
from .schemas import ValidationFailure
from .synthesizer import GeminiQuotaExceededError
from .tribe_client import TribeClient, TribeServiceError

# Modal SDK is only available inside the Modal container. Keep the
# import lazy so local pytest runs (which don't have the modal package
# installed at test time) don't crash on module load.
try:
    import modal as _modal  # type: ignore
    _MODAL_AVAILABLE = True
except ImportError:
    _modal = None  # type: ignore
    _MODAL_AVAILABLE = False

configure_logging()
log = get_logger(__name__)

app = FastAPI(
    title="Aesthesis backend",
    version="0.3.0.0",
    description=(
        "Brain-grounded UX analysis via TRIBE v2. Step 2 (Assess) backend "
        "— accepts a single MP4 upload, returns the full results-page JSON."
    ),
)

# CORS for the Next.js frontend. The browser sends a multipart POST against
# /api/analyze from a different origin (e.g. localhost:3000 → localhost:8000)
# so without this the preflight + actual request both fail before they ever
# reach the orchestrator. Origins come from config — never hardcoded here.
# The regex covers Vercel preview deploys (aesthesis-frontend-<hash>-<team>
# .vercel.app), which an exact-match list would miss and the browser would
# surface as TypeError: Failed to fetch.
_cfg = get_config()
_cors_origins = _cfg.cors_allow_origins
_cors_regex = _cfg.cors_allow_origin_regex
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=False,  # we don't use cookies; keeps wildcard combos legal
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Aesthesis-Run-Id", "X-Aesthesis-Elapsed-Ms"],
)
log.info("CORS configured", extra={"step": "startup",
                                    "cors_allow_origins": _cors_origins,
                                    "cors_allow_origin_regex": _cors_regex})


@app.middleware("http")
async def _request_log_middleware(request: Request, call_next):
    """Log every incoming request with method, path, and elapsed time. The
    body of /api/analyze gets logged in the endpoint itself; this middleware
    is the entry/exit boundary log so nothing slips past unobserved."""
    t0 = time.perf_counter()
    log.debug(
        "http request begin",
        extra={"step": "http", "method": request.method,
               "path": request.url.path,
               "client": request.client.host if request.client else None},
    )
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        log.exception(
            "http request crashed",
            extra={"step": "http", "method": request.method,
                   "path": request.url.path,
                   "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
        )
        raise
    log.info(
        "http request end",
        extra={"step": "http", "method": request.method,
               "path": request.url.path,
               "status_code": response.status_code,
               "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
    )
    return response


@app.get("/health")
async def health() -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {
        "status": "ok",
        "tribe_service_url": cfg.tribe_service_url,
        "max_duration_s": cfg.max_duration_s,
        "max_upload_bytes": cfg.max_upload_bytes,
    }
    try:
        tribe_health = await TribeClient(cfg.tribe_service_url, timeout_s=5).health()
        out["tribe"] = tribe_health
    except Exception as e:  # noqa: BLE001
        out["tribe"] = {"status": "unreachable", "error": str(e)}
    return out


@app.get("/api/warmup")
async def warmup() -> dict[str, Any]:
    """Wake the Tribe GPU container AND load the model ahead of analyze.

    Sends a tiny synthetic 3-second MP4 to Tribe's /process_video_timeline
    so the TRIBE v2 + V-JEPA-2 weights load during landing-page mount
    instead of inside the user's first analyze call. Without this, a
    cold first analyze pays:
      - ~28s of TRIBE v2 + V-JEPA-2 weight loading (first inference only)
      - the actual V-JEPA encoding work for the user's video
      - orchestrator post-processing + 2 Gemini calls
    On videos > ~30s, the combined wall time exceeds Modal's ~150s sync
    web-proxy timeout — the proxy drops the connection and the browser
    sees `TypeError: Failed to fetch`, even though Tribe is still
    happily processing on the other side.

    Pinging /health alone (the previous warmup behavior) wakes the
    container but does NOT trigger model load — Tribe loads weights
    lazily on the first inference call. So we have to send a real (if
    tiny) inference to pay the model-load cost up front.

    The synthetic MP4 is generated via ffmpeg lavfi (testsrc, 320x240,
    3s @ 10fps, ~50KB H.264). Tribe inference on this clip is ~3-8s
    once the model is loaded; the first call from cold pays ~30-50s.

    Fire-and-forget from the client. Always returns 200 with a body
    describing the outcome; the frontend's prewarmTribe() ignores
    failures since they just mean the user's first analyze pays the
    cold-start tax instead.
    """
    cfg = get_config()
    t0 = time.perf_counter()

    # Step 1: synthesize a tiny MP4 via ffmpeg lavfi (no fixture file
    # on disk). Same pattern the screenshot test suite uses — we know
    # ffmpeg can generate a valid H.264 MP4 from this command line.
    tmp_path = cfg.upload_dir / f"warmup-{uuid.uuid4().hex[:8]}.mp4"
    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "testsrc=duration=3:size=320x240:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(tmp_path),
        ]
        res = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=15,
        )
        if res.returncode != 0 or not tmp_path.exists():
            err = res.stderr.decode(errors="replace")[:300] if res.stderr else "no stderr"
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            log.warning(
                "warmup mp4 synth failed (rc=%d): %s",
                res.returncode, err,
                extra={"step": "warmup", "elapsed_ms": elapsed_ms,
                       "stage": "synth"},
            )
            return {
                "ok": False, "stage": "synth", "error": err,
                "elapsed_ms": elapsed_ms,
            }
        log.debug(
            "warmup mp4 synthesized",
            extra={"step": "warmup", "stage": "synth",
                   "path": str(tmp_path),
                   "size_bytes": tmp_path.stat().st_size},
        )
    except Exception as e:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.warning(
            "warmup mp4 synth raised: %s", e,
            extra={"step": "warmup", "elapsed_ms": elapsed_ms,
                   "stage": "synth", "error_type": type(e).__name__},
        )
        return {
            "ok": False, "stage": "synth", "error": str(e),
            "elapsed_ms": elapsed_ms,
        }

    # Step 2: real inference call against Tribe — this is the load-bearing
    # step that triggers model load. Long timeout: cold-start can be
    # 30-60s of weight load + a few seconds of inference.
    warmup_run_id = f"warmup-{uuid.uuid4().hex[:8]}"
    try:
        client = TribeClient(cfg.tribe_service_url, timeout_s=240)
        log.info(
            "warmup posting tiny clip to Tribe",
            extra={"step": "warmup", "stage": "tribe",
                   "run_id": warmup_run_id,
                   "tribe_url": cfg.tribe_service_url,
                   "size_bytes": tmp_path.stat().st_size},
        )
        await client.process_video_timeline(tmp_path, run_id=warmup_run_id)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.info(
            "warmup ok (Tribe model loaded)",
            extra={"step": "warmup", "stage": "tribe",
                   "run_id": warmup_run_id, "elapsed_ms": elapsed_ms},
        )
        return {"ok": True, "elapsed_ms": elapsed_ms}
    except Exception as e:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.warning(
            "warmup tribe call failed: %s", e,
            extra={"step": "warmup", "stage": "tribe",
                   "run_id": warmup_run_id, "elapsed_ms": elapsed_ms,
                   "error_type": type(e).__name__},
        )
        return {
            "ok": False, "stage": "tribe", "error": str(e),
            "elapsed_ms": elapsed_ms,
        }
    finally:
        # Best-effort cleanup of the synthetic MP4. unlink(missing_ok=True)
        # requires Python 3.8+; we're on 3.11. Wrap in try/except anyway
        # so a stray permission glitch doesn't crash the warmup endpoint.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/api/analyze")
async def analyze(
    video: UploadFile = File(..., description="MP4 of the demo to analyze"),
    goal: str | None = Form(default=None),
) -> JSONResponse:
    """Spawn a background analyze job and return a job_id immediately.

    The actual ~6-13s warm / 80-300s real-video analyze work runs inside
    the ``analyze_blocking`` Modal function (see modal_app.py), invoked
    via ``Function.spawn`` so it doesn't go through Modal's web proxy.
    Modal's web proxy has a hard ~150s sync timeout on web endpoints —
    that's what was killing real-video analyzes mid-pipeline. Spawn'd
    functions only obey the function's own timeout=600s, so we have
    full headroom for cold Tribe + V-JEPA + 2 Gemini calls.

    The browser polls ``/api/analyze/status/{job_id}`` every ~3s until
    status flips to ``done`` or ``failed``. Each individual poll is a
    50–200ms request, well under the proxy ceiling.

    Returns ``{"job_id": "fc-...", "run_id": "...", "status": "queued"}``.
    """
    cfg = get_config()
    rid = str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "endpoint",
                 # NOTE: don't use the key "filename" — it's a reserved
                 # LogRecord attribute (Python logging crashes with
                 # "Attempt to overwrite 'filename' in LogRecord").
                 "video_filename": video.filename,
                 "content_type": video.content_type,
                 "goal_present": goal is not None}
    log.info("/api/analyze received (async spawn)", extra=log_extra)

    if not _MODAL_AVAILABLE:
        # Local dev fallback: the Modal SDK isn't installed (or this is
        # a non-Modal run). Fall back to the legacy synchronous flow
        # so local uvicorn dev still works. Production always runs
        # inside a Modal container so this branch is a dev convenience.
        log.warning(
            "Modal SDK unavailable — falling back to synchronous /api/analyze",
            extra=log_extra,
        )
        return await _analyze_sync_fallback(video, goal, rid, cfg, log_extra)

    t0 = time.perf_counter()
    try:
        video_bytes = await video.read()
        if len(video_bytes) > cfg.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=ValidationFailure(
                    field="video",
                    error=(
                        f"upload {len(video_bytes)} bytes exceeds "
                        f"max_upload_bytes={cfg.max_upload_bytes}"
                    ),
                ).model_dump(),
            )
        log.info(
            "spawning analyze_blocking — size=%d",
            len(video_bytes),
            extra={**log_extra, "size_bytes": len(video_bytes)},
        )

        # Look up the deployed analyze_blocking function by app + name.
        # Cached after first call, ~50ms.
        analyze_fn = _modal.Function.from_name(
            "aesthesis-orchestrator", "analyze_blocking",
        )
        call = analyze_fn.spawn(video_bytes, goal, rid)
        spawn_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.info(
            "analyze_blocking spawned — job_id=%s spawn_ms=%.1f",
            call.object_id, spawn_ms,
            extra={**log_extra, "job_id": call.object_id,
                   "spawn_ms": spawn_ms},
        )

        return JSONResponse(
            {"job_id": call.object_id, "run_id": rid, "status": "queued"},
            headers={
                "X-Aesthesis-Run-Id": rid,
                "X-Aesthesis-Job-Id": call.object_id,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("failed to spawn analyze job", extra=log_extra)
        raise HTTPException(
            status_code=500,
            detail=f"failed to spawn analyze job: {type(e).__name__}: {e}",
        ) from e


async def _analyze_sync_fallback(
    video: UploadFile,
    goal: str | None,
    rid: str,
    cfg: Any,
    log_extra: dict,
) -> JSONResponse:
    """Legacy synchronous /api/analyze path. Used only when the Modal
    SDK isn't available (local uvicorn dev). Production always uses
    the spawn + poll flow because Modal's web proxy times out at 150s.
    """
    run_dir = cfg.upload_dir / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "video.mp4"
    t0 = time.perf_counter()
    try:
        with path.open("wb") as out:
            shutil.copyfileobj(video.file, out)
        response = await run_analysis(
            cfg=cfg, video=path, goal=goal, run_id=rid,
        )
        return JSONResponse(
            response.model_dump(mode="json"),
            headers={
                "X-Aesthesis-Run-Id": rid,
                "X-Aesthesis-Elapsed-Ms": f"{response.elapsed_ms:.2f}",
            },
        )
    except OrchestratorError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=ValidationFailure(field=e.field, error=str(e)).model_dump(),
        ) from e
    except TribeServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except GeminiQuotaExceededError as e:
        retry_after = f"{int(e.retry_delay_s) + 1}" if e.retry_delay_s else "60"
        raise HTTPException(
            status_code=503, detail=str(e),
            headers={"Retry-After": retry_after},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.exception("sync fallback failed", extra=log_extra)
        raise HTTPException(
            status_code=500, detail=f"internal error: {e}",
        ) from e
    finally:
        if cfg.cleanup_uploads:
            shutil.rmtree(run_dir, ignore_errors=True)


@app.get("/api/analyze/status/{job_id}")
async def analyze_status(job_id: str) -> JSONResponse:
    """Poll the status of a spawned analyze job.

    Returns one of:
      - ``{"status": "running"}`` — job not yet complete
      - ``{"status": "done", "result": <AnalyzeResponse dict>}`` — finished
      - ``{"status": "failed", "error": str}`` — analyze_blocking raised
      - ``{"status": "expired", "error": str}`` — Modal GC'd the result

    Each poll is a 50–200ms request — well under any proxy ceiling.
    Browser polls every ~3s; gives up after 8 min.
    """
    log_extra = {"step": "status", "job_id": job_id}

    if not _MODAL_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="status polling requires the Modal runtime (production only)",
        )

    try:
        call = _modal.functions.FunctionCall.from_id(job_id)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "invalid job_id %s: %s", job_id, e,
            extra={**log_extra, "error_type": type(e).__name__},
        )
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")

    # Non-blocking poll. timeout=0 returns immediately if done; otherwise
    # raises a Modal-side timeout exception (variant depending on SDK).
    try:
        result = call.get(timeout=0)
    except _modal.exception.OutputExpiredError as e:  # type: ignore[attr-defined]
        log.info("job_id %s result expired", job_id, extra=log_extra)
        return JSONResponse(
            {"status": "expired",
             "error": "Result was garbage-collected by Modal (24h+). Re-run analyze."},
            status_code=410,
        )
    except GeminiQuotaExceededError as e:
        log.error(
            "job_id %s Gemini quota exceeded — retry_delay=%s",
            job_id, e.retry_delay_s, extra=log_extra,
        )
        retry_after = int(e.retry_delay_s) + 1 if e.retry_delay_s else 60
        return JSONResponse(
            {"status": "failed",
             "error": (
                 "Gemini API quota exhausted. Switch "
                 "GEMINI_MODEL_INSIGHTS / GEMINI_MODEL_VERDICT to a "
                 "higher-quota model (e.g. gemini-2.5-flash) or wait "
                 f"~{retry_after}s for the per-minute window to reset."
             ),
             "retry_after_s": retry_after},
        )
    except TribeServiceError as e:
        log.error("job_id %s TRIBE error: %s", job_id, e, extra=log_extra)
        return JSONResponse({"status": "failed",
                             "error": f"TRIBE service error: {e}"})
    except OrchestratorError as e:
        return JSONResponse({"status": "failed",
                             "error": f"orchestrator error: {e}"})
    except Exception as e:  # noqa: BLE001
        # Modal raises a few flavors of timeout / RemoteError when
        # get(timeout=0) finds the call still running. Distinguish
        # by message — the still-running case is the common path.
        msg = str(e)
        msg_lower = msg.lower()
        is_still_running = (
            "did not complete" in msg_lower
            or "still running" in msg_lower
            or "timeout" in msg_lower
            or msg == ""
        )
        type_name = type(e).__name__
        # Modal-specific exception names that mean "not done yet".
        if type_name in {
            "FunctionTimeoutError",
            "OutputTimeoutError",
            "TimeoutError",
        }:
            is_still_running = True

        if is_still_running:
            return JSONResponse({"status": "running"})

        log.exception(
            "job_id %s failed with %s: %s",
            job_id, type_name, msg, extra=log_extra,
        )
        return JSONResponse({"status": "failed",
                             "error": f"{type_name}: {msg}"})

    log.info(
        "job_id %s done — n_insights=%d",
        job_id, len(result.get("insights", [])) if isinstance(result, dict) else -1,
        extra=log_extra,
    )
    return JSONResponse({"status": "done", "result": result})



def make_app() -> FastAPI:
    """Hook for ``uvicorn aesthesis.main:make_app --factory``. Identical to
    importing the module-level ``app``."""
    return app
