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

import logging
import shutil
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
    version="0.2.0.0",
    description=(
        "Brain-grounded UX analysis via TRIBE v2. Step 2 (Assess) backend "
        "— accepts a single MP4 upload, returns the full results-page JSON."
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


def make_app() -> FastAPI:
    """Hook for ``uvicorn aesthesis.main:make_app --factory``. Identical to
    importing the module-level ``app``."""
    return app
