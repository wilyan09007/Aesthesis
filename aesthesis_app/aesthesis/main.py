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
from .tribe_client import TribeClient, TribeServiceError

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
    cfg = get_config()
    rid = str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "endpoint",
                 # NOTE: don't use the key "filename" — it's a reserved
                 # LogRecord attribute (Python logging crashes with
                 # "Attempt to overwrite 'filename' in LogRecord").
                 "video_filename": video.filename,
                 "content_type": video.content_type,
                 "goal_present": goal is not None}
    log.info("/api/analyze received", extra=log_extra)

    # Persist the upload under cfg.upload_dir / run_id / .
    run_dir = cfg.upload_dir / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "video.mp4"

    t0 = time.perf_counter()
    try:
        with path.open("wb") as out:
            shutil.copyfileobj(video.file, out)
        log.debug(
            "upload persisted",
            extra={**log_extra, "size_bytes": path.stat().st_size,
                   "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
        )

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
        log.warning("orchestrator validation failed: %s", e, extra=log_extra)
        raise HTTPException(
            status_code=e.status_code,
            detail=ValidationFailure(field=e.field, error=str(e)).model_dump(),
        ) from e
    except TribeServiceError as e:
        log.error("TRIBE service error: %s", e, extra=log_extra)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("unexpected error in /api/analyze", extra=log_extra)
        raise HTTPException(status_code=500, detail=f"internal error: {e}") from e
    finally:
        if cfg.cleanup_uploads:
            try:
                shutil.rmtree(run_dir, ignore_errors=True)
                log.debug("upload cleaned up", extra=log_extra)
            except Exception:  # noqa: BLE001
                log.warning("upload cleanup failed", exc_info=True, extra=log_extra)



def make_app() -> FastAPI:
    """Hook for ``uvicorn aesthesis.main:make_app --factory``. Identical to
    importing the module-level ``app``."""
    return app
