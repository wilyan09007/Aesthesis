"""FastAPI app — public surface for the Aesthesis backend.

Endpoints (post-Phase-2):
    GET  /health                            — liveness + downstream TRIBE liveness
    POST /api/analyze                       — Step 2 skip path (multipart MP4)
    POST /api/run                           — Phase 2: start a URL capture
    WS   /api/stream/{run_id}               — Phase 2: live frames + lifecycle
    GET  /api/run/{run_id}/video            — Phase 2: download captured MP4
    POST /api/analyze/by-run/{run_id}       — Phase 2: analyze captured MP4 by ref
    GET  /api/cached-demos                  — Phase 2: D29 stage-day fallback list

DESIGN.md §10 Q11 / §17 / §§4.1, 4.2, 4.2b. Phase 2 capture pipeline
adds 5 endpoints; the existing skip-path /api/analyze is unchanged.

Pre-pivot this accepted ``video_a`` + ``video_b`` and returned an A/B
comparison. The pivot to single-video collapsed it — see DESIGN.md §17.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI, File, Form, HTTPException, Request, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .capture import runner as capture_runner
from .capture.protocol import (
    AnalyzeByRunRequest, CachedDemoEntry,
    RunRequest, RunStartedResponse,
)
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
        "+ Phase 2 capture pipeline — POST a URL, watch a live BrowserUse "
        "agent drive Chromium, MP4 streams to the existing analyzer."
    ),
)

# CORS for the Next.js frontend. The browser sends a multipart POST against
# /api/analyze from a different origin (e.g. localhost:3000 → localhost:8000)
# so without this the preflight + actual request both fail before they ever
# reach the orchestrator. Origins come from config — never hardcoded here.
_cors_origins = get_config().cors_allow_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # we don't use cookies; keeps wildcard combos legal
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Aesthesis-Run-Id", "X-Aesthesis-Elapsed-Ms"],
)
log.info("CORS configured", extra={"step": "startup",
                                    "cors_allow_origins": _cors_origins})


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


# ─── Phase 2: capture pipeline endpoints ───────────────────────────────────


@app.post("/api/run", response_model=RunStartedResponse)
async def start_capture_run(req: RunRequest) -> RunStartedResponse:
    """Start a Phase 2 capture run (DESIGN.md §§4.1, 4.2, 4.2b).

    Spawns the BrowserUse subprocess, returns immediately with a run_id.
    Frontend connects to ``ws://.../api/stream/{run_id}`` to receive
    live JPEG frames as binary WS messages and lifecycle events as JSON
    control messages. On ``capture_complete``, fetch the MP4 via
    ``GET /api/run/{run_id}/video`` and call
    ``POST /api/analyze/by-run/{run_id}``.

    D19: caps active captures at 1 per backend instance. Second concurrent
    request gets 409 with the active run_id in the body.
    """
    cfg = get_config()
    log.info(
        "/api/run received",
        extra={"step": "endpoint", "url": str(req.url),
               "goal_present": req.goal is not None,
               "n_cookies": len(req.auth.cookies) if (req.auth and req.auth.cookies) else 0},
    )
    try:
        runner = await capture_runner.start_run(req, cfg=cfg)
    except capture_runner.CaptureInProgressError as e:
        log.warning("/api/run rejected — capture in progress",
                    extra={"step": "endpoint", "active_run_id": e.active_run_id})
        raise HTTPException(
            status_code=409,
            detail={"error": "capture_in_progress",
                    "active_run_id": e.active_run_id,
                    "message": str(e)},
        ) from e

    return RunStartedResponse(run_id=runner.run_id)


@app.websocket("/api/stream/{run_id}")
async def stream_capture(ws: WebSocket, run_id: str) -> None:
    """Live capture stream (D9 adaptive 5-tier, D30c binary frames,
    D32 lifecycle replay on connect, D27 3s grace on disconnect).

    Protocol:
    - Binary WS messages = raw JPEG bytes (one frame each).
    - JSON WS messages = control: stream_degraded / capture_complete /
      capture_failed / agent_event.

    On accept(), backend immediately sends the last lifecycle event (if any)
    so a reconnecting client doesn't miss capture_complete fired during
    the gap. On disconnect, backend arms a 3s grace timer; if no other
    subscriber connects within 3s, the subprocess is SIGKILLed (D27).
    """
    runner = capture_runner.get_runner(run_id)
    if runner is None:
        log.warning(
            "ws: unknown run_id, refusing connection",
            extra={"step": "ws", "run_id": run_id},
        )
        await ws.close(code=4404, reason="unknown run_id")
        return

    await ws.accept()
    log.info(
        "ws: connected",
        extra={"step": "ws", "run_id": run_id,
               "n_subscribers_after": len(runner.subscribers) + 1},
    )
    await runner.add_subscriber(ws)

    try:
        # We never expect the client to send anything on this WS — it's
        # backend->frontend. Hold the connection open until the client
        # disconnects (or backend closes it on completion).
        while True:
            await ws.receive_text()
    except WebSocketDisconnect as e:
        log.info(
            "ws: client disconnected",
            extra={"step": "ws", "run_id": run_id,
                   "code": e.code, "reason": getattr(e, "reason", "")},
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "ws: receive loop crashed: %s", e,
            extra={"step": "ws", "run_id": run_id},
        )
    finally:
        await runner.remove_subscriber(ws)
        log.info(
            "ws: subscriber removed",
            extra={"step": "ws", "run_id": run_id,
                   "n_subscribers_after": len(runner.subscribers)},
        )


@app.get("/api/run/{run_id}/video")
async def download_run_video(run_id: str) -> FileResponse:
    """Serve the captured MP4 for the given run_id.

    The captured file lives at ``cfg.upload_dir/{run_id}/video.mp4`` from
    when the subprocess finalized it. Returns 404 if the file isn't
    there yet (capture still running) or if the run_id is unknown.

    Cleanup is owned by /api/analyze/by-run/{run_id} — this endpoint
    does not delete on read.
    """
    cfg = get_config()
    mp4_path = cfg.upload_dir / run_id / "video.mp4"
    if not mp4_path.exists():
        log.warning(
            "/api/run/{id}/video: not found",
            extra={"step": "endpoint", "run_id": run_id, "path": str(mp4_path)},
        )
        raise HTTPException(
            status_code=404,
            detail={"error": "video_not_ready",
                    "run_id": run_id,
                    "message": "captured video not yet available — wait for capture_complete or check run_id"},
        )
    log.info(
        "/api/run/{id}/video: serving",
        extra={"step": "endpoint", "run_id": run_id,
               "size_bytes": mp4_path.stat().st_size},
    )
    return FileResponse(
        path=str(mp4_path),
        media_type="video/mp4",
        filename=f"{run_id}.mp4",
    )


@app.post("/api/analyze/by-run/{run_id}")
async def analyze_by_run(run_id: str, req: AnalyzeByRunRequest | None = None) -> JSONResponse:
    """Analyze a captured MP4 by run_id reference (D11).

    Loads the MP4 from ``cfg.upload_dir/{run_id}/video.mp4`` (created by
    the capture subprocess), threads the optional actions.jsonl into the
    orchestrator (D15 action stamping), and runs the same TRIBE + Gemini
    pipeline as ``/api/analyze``.

    D33: cleans the run_dir only on success. On failure, artifacts persist
    for debug — the asymmetry vs the multipart skip path (which always
    cleans) is intentional because capture-then-analyze is far more
    expensive to reproduce.
    """
    cfg = get_config()
    log_extra = {"run_id": run_id, "step": "endpoint"}
    log.info("/api/analyze/by-run received", extra=log_extra)

    run_dir = cfg.upload_dir / run_id
    mp4_path = run_dir / "video.mp4"
    actions_path = run_dir / "actions.jsonl"

    if not mp4_path.exists():
        log.warning(
            "/api/analyze/by-run: video missing",
            extra={**log_extra, "path": str(mp4_path)},
        )
        raise HTTPException(
            status_code=404,
            detail={"error": "video_not_ready",
                    "run_id": run_id,
                    "message": "captured video not found at expected path — capture may not be complete"},
        )

    goal = req.goal if req else None
    log.debug(
        "/api/analyze/by-run: dispatching",
        extra={**log_extra, "video_path": str(mp4_path),
               "actions_path": str(actions_path) if actions_path.exists() else None,
               "goal_present": goal is not None},
    )

    t0 = time.perf_counter()
    try:
        response = await run_analysis(
            cfg=cfg, video=mp4_path,
            goal=goal, run_id=run_id,
            action_log_path=actions_path if actions_path.exists() else None,
        )
        # D33: success-only cleanup
        if cfg.cleanup_uploads:
            try:
                shutil.rmtree(run_dir, ignore_errors=True)
                log.info(
                    "/api/analyze/by-run: cleanup done",
                    extra={**log_extra, "run_dir": str(run_dir)},
                )
            except Exception:  # noqa: BLE001
                log.warning(
                    "/api/analyze/by-run: cleanup failed",
                    exc_info=True, extra=log_extra,
                )
        return JSONResponse(
            response.model_dump(mode="json"),
            headers={
                "X-Aesthesis-Run-Id": run_id,
                "X-Aesthesis-Elapsed-Ms": f"{response.elapsed_ms:.2f}",
            },
        )
    except OrchestratorError as e:
        log.warning(
            "/api/analyze/by-run: orchestrator validation failed: %s — RETAINING ARTIFACTS for debug (D33)",
            e,
            extra={**log_extra, "run_dir": str(run_dir)},
        )
        raise HTTPException(
            status_code=e.status_code,
            detail=ValidationFailure(field=e.field, error=str(e)).model_dump(),
        ) from e
    except TribeServiceError as e:
        log.error(
            "/api/analyze/by-run: TRIBE error: %s — RETAINING ARTIFACTS for debug (D33)",
            e,
            extra={**log_extra, "run_dir": str(run_dir)},
        )
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception(
            "/api/analyze/by-run: unexpected — RETAINING ARTIFACTS for debug (D33)",
            extra={**log_extra, "run_dir": str(run_dir)},
        )
        raise HTTPException(status_code=500, detail=f"internal error: {e}") from e


@app.get("/api/cached-demos/{filename}")
async def download_cached_demo(filename: str) -> FileResponse:
    """Serve one cached demo MP4 by filename (D29 fallback download).

    The frontend's "Use cached demo" button hits this after a
    capture_failed when the requested URL has a manifest entry. The MP4
    is then routed through the existing /api/analyze multipart skip
    path. Filename comes from MANIFEST.json — we still validate against
    path traversal (no .., no /) before opening.
    """
    cfg = get_config()
    # Path traversal guard — reject anything that isn't a flat filename
    if "/" in filename or "\\" in filename or ".." in filename:
        log.warning(
            "/api/cached-demos/{file}: rejecting path-traversal attempt",
            extra={"step": "endpoint", "filename": filename},
        )
        raise HTTPException(status_code=400, detail="invalid filename")
    mp4_path = cfg.cached_demos_dir / filename
    if not mp4_path.exists() or not mp4_path.is_file():
        log.warning(
            "/api/cached-demos/{file}: not found",
            extra={"step": "endpoint", "filename": filename, "path": str(mp4_path)},
        )
        raise HTTPException(status_code=404, detail="cached demo not found")
    log.info(
        "/api/cached-demos/{file}: serving",
        extra={"step": "endpoint", "filename": filename,
               "size_bytes": mp4_path.stat().st_size},
    )
    return FileResponse(
        path=str(mp4_path),
        media_type="video/mp4",
        filename=filename,
    )


@app.get("/api/cached-demos", response_model=list[CachedDemoEntry])
async def list_cached_demos() -> list[CachedDemoEntry]:
    """D29: enumerate canonical demo MP4s for the stage-day fallback button.

    Reads ``{cached_demos_dir}/MANIFEST.json`` which is a list of
    ``{url, label, mp4_filename}`` entries. The frontend offers a
    one-click "Use cached demo" button on ``capture_failed`` when the
    requested URL matches an entry's url.

    Returns [] if the dir or manifest doesn't exist — the fallback is
    optional infrastructure.
    """
    cfg = get_config()
    manifest_path = cfg.cached_demos_dir / "MANIFEST.json"
    if not manifest_path.exists():
        log.debug(
            "/api/cached-demos: no manifest",
            extra={"step": "endpoint", "path": str(manifest_path)},
        )
        return []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [CachedDemoEntry(**e) for e in raw]
        log.info(
            "/api/cached-demos: served",
            extra={"step": "endpoint", "n_entries": len(entries)},
        )
        return entries
    except Exception as e:  # noqa: BLE001 — manifest JSON busted
        log.warning(
            "/api/cached-demos: manifest unparseable: %s",
            e,
            extra={"step": "endpoint", "path": str(manifest_path)},
        )
        return []


def make_app() -> FastAPI:
    """Hook for ``uvicorn aesthesis.main:make_app --factory``. Identical to
    importing the module-level ``app``."""
    return app
