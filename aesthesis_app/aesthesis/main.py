"""FastAPI app — public surface for the Aesthesis backend.

Endpoints:
    GET  /health           — liveness + downstream TRIBE liveness
    POST /api/analyze      — the load-bearing endpoint

DESIGN.md §10 Q11: multipart upload to this endpoint, written to a tmp dir,
then forwarded by the orchestrator to the TRIBE service.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
    version="0.1.0.0",
    description=(
        "Brain-judged A/B comparison via TRIBE v2. Step 2 (Assess) "
        "backend — accepts two MP4 uploads, returns the full "
        "results-page JSON."
    ),
)


@app.get("/health")
async def health() -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {
        "status": "ok",
        "tribe_service_url": cfg.tribe_service_url,
        "gemini_mock_mode": cfg.gemini_mock_mode,
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
    video_a: UploadFile = File(..., description="MP4 for Version A"),
    video_b: UploadFile = File(..., description="MP4 for Version B"),
    goal: str | None = Form(default=None),
) -> JSONResponse:
    cfg = get_config()
    rid = str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "endpoint",
                 "filename_a": video_a.filename,
                 "filename_b": video_b.filename}
    log.info("/api/analyze received", extra=log_extra)

    # Persist both uploads to disk under cfg.upload_dir / run_id / .
    run_dir = cfg.upload_dir / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    path_a = run_dir / "a.mp4"
    path_b = run_dir / "b.mp4"

    t0 = time.perf_counter()
    try:
        for fh, path in ((video_a, path_a), (video_b, path_b)):
            with path.open("wb") as out:
                shutil.copyfileobj(fh.file, out)
        log.debug(
            "uploads persisted",
            extra={**log_extra,
                   "size_a": path_a.stat().st_size, "size_b": path_b.stat().st_size,
                   "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
        )

        response = await run_analysis(
            cfg=cfg, video_a=path_a, video_b=path_b,
            goal=goal, run_id=rid,
        )
        return JSONResponse(response.model_dump(mode="json"))
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
                # Wait briefly so any frame-extraction child process can finish.
                shutil.rmtree(run_dir, ignore_errors=True)
                log.debug("uploads cleaned up", extra=log_extra)
            except Exception:  # noqa: BLE001
                log.warning("upload cleanup failed", exc_info=True, extra=log_extra)


def make_app() -> FastAPI:
    """Hook for `uvicorn aesthesis.main:make_app --factory`. Identical to
    importing the module-level `app`."""
    return app
