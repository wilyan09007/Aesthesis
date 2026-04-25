"""ARQ worker settings.

The GPU worker loads `Resources` once on `on_startup` and registers a
single async task `process_video_timeline_task` that the API layer
enqueues. `max_jobs=1` per worker (DESIGN.md D6) — TRIBE serializes
inference through one model in VRAM.

ARQ is optional in dev: if `arq` isn't installed, importing this module
prints a one-line warning. The FastAPI app can still run synchronously
via the `/process_video_timeline` endpoint, which calls the pipeline
inline — useful in mock mode and for local tests.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .init_resources import Resources, load_resources
from .logging_config import configure_logging
from .pipeline import process_video_timeline

log = logging.getLogger(__name__)


async def process_video_timeline_task(
    ctx: dict,
    *,
    video_path: str,
    window_trs: int = 4,
    step_trs: int = 1,
    run_id: str | None = None,
) -> dict:
    """ARQ task entrypoint. `ctx['resources']` was set by `startup`."""
    resources: Resources = ctx["resources"]
    return process_video_timeline(
        Path(video_path),
        resources,
        window_trs=window_trs,
        step_trs=step_trs,
        run_id=run_id,
    )


async def startup(ctx: dict) -> None:
    configure_logging()
    log.info("ARQ worker starting up — loading resources")
    ctx["resources"] = load_resources()
    log.info("ARQ worker ready")


async def shutdown(ctx: dict) -> None:
    log.info("ARQ worker shutting down")


# Importing arq is wrapped — the module may run in environments where ARQ
# isn't installed (laptop dev, unit tests). In those cases the API layer
# simply skips the enqueue endpoints and runs sync.
try:
    from arq.connections import RedisSettings  # type: ignore

    class WorkerSettings:
        functions = [process_video_timeline_task]
        on_startup = startup
        on_shutdown = shutdown
        max_jobs = int(os.getenv("GPU_WORKER_MAX_JOBS", "1"))
        job_timeout = int(os.getenv("TRIBE_JOB_TIMEOUT", "300"))
        redis_settings = RedisSettings(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
        )

    HAS_ARQ = True
except ImportError:  # pragma: no cover — laptop / test env
    HAS_ARQ = False
    log.warning("arq not installed; async /enqueue endpoints will return 501")
