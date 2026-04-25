"""End-to-end orchestrator for /api/analyze.

Sequence (DESIGN.md §3, Step 2 panel — serial per D6):
    1. validate both MP4s (parallel — ffmpeg.probe is fast)
    2. POST video A to TRIBE /process_video_timeline (await ~3-8s)
    3. POST video B to TRIBE /process_video_timeline (await ~3-8s)
    4. extract events for A and B (deterministic, ~10ms each)
    5. extract per-event screenshots
    6. Gemini insight call A (await ~1-3s)
    7. Gemini insight call B (await ~1-3s)
    8. compute aggregate metrics
    9. Gemini verdict call (await ~1-2s)
   10. assemble AnalyzeResponse via output_builder

Total wall time: ~12-25s for two 30s clips. Verbose log lines emit at
every boundary so a slow run is easy to diagnose.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from .config import AppConfig
from .events import extract_events
from .output_builder import build_response
from .schemas import AnalyzeResponse, Event
from .screenshots import extract_frame
from .synthesizer import synthesize
from .tribe_client import TribeClient
from .validation import ValidationResult, validate_upload

log = logging.getLogger(__name__)


async def _attach_screenshots(
    events: list[Event],
    video: Path,
    *,
    work_dir: Path,
    run_id: str,
    version: str,
) -> None:
    """Mutates `events` in place — attaches `.screenshot_path` for every
    event whose timestamp we can extract a frame for."""
    work_dir.mkdir(parents=True, exist_ok=True)
    log.debug(
        "extracting screenshots",
        extra={"step": "screenshots", "run_id": run_id, "version": version,
               "n_events": len(events)},
    )

    def _extract(e: Event) -> None:
        out = work_dir / f"{version.lower()}_t{e.timestamp_s:.2f}.jpg"
        path = extract_frame(video, e.timestamp_s, out)
        if path is not None:
            e.screenshot_path = str(path)

    # ffmpeg work is CPU-bound and short; run sequentially in this thread.
    # Doing it on a thread pool would only matter if the cap is much higher.
    for e in events:
        try:
            _extract(e)
        except Exception as ex:  # noqa: BLE001
            log.debug("screenshot failed at t=%.2f: %s", e.timestamp_s, ex)


async def run_analysis(
    *,
    cfg: AppConfig,
    video_a: Path,
    video_b: Path,
    goal: str | None = None,
    run_id: str | None = None,
) -> AnalyzeResponse:
    rid = run_id or str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "orchestrator"}
    log.info(
        "analysis begin",
        extra={**log_extra, "video_a": str(video_a), "video_b": str(video_b),
               "goal": goal, "tribe_url": cfg.tribe_service_url},
    )

    overall_t0 = time.perf_counter()

    # ── Step 1: validate ────────────────────────────────────────────────
    val_a, val_b = await asyncio.gather(
        asyncio.to_thread(validate_upload, video_a, cfg),
        asyncio.to_thread(validate_upload, video_b, cfg),
    )
    if not val_a.ok:
        raise OrchestratorError(field="video_a", message=val_a.error or "invalid")
    if not val_b.ok:
        raise OrchestratorError(field="video_b", message=val_b.error or "invalid")
    log.info(
        "both videos validated",
        extra={**log_extra,
               "duration_a": val_a.duration_s, "duration_b": val_b.duration_s},
    )

    # ── Steps 2-3: TRIBE serial (D6) ────────────────────────────────────
    client = TribeClient(cfg.tribe_service_url, timeout_s=cfg.tribe_request_timeout_s)
    log.info("posting to TRIBE: video A", extra={**log_extra, "version": "A"})
    timeline_a = await client.process_video_timeline(video_a, run_id=rid)
    log.info("posting to TRIBE: video B", extra={**log_extra, "version": "B"})
    timeline_b = await client.process_video_timeline(video_b, run_id=rid)

    # ── Step 4: events ──────────────────────────────────────────────────
    events_a = extract_events(timeline_a, "A")
    events_b = extract_events(timeline_b, "B")

    # ── Step 5: screenshots ─────────────────────────────────────────────
    work_dir = cfg.upload_dir / rid / "frames"
    await _attach_screenshots(events_a, video_a, work_dir=work_dir, run_id=rid, version="A")
    await _attach_screenshots(events_b, video_b, work_dir=work_dir, run_id=rid, version="B")

    # ── Steps 6-9: Gemini ───────────────────────────────────────────────
    synth = await synthesize(
        events_a, events_b, timeline_a, timeline_b,
        goal=goal, cfg=cfg, run_id=rid,
    )

    elapsed_ms = (time.perf_counter() - overall_t0) * 1000.0

    response = build_response(
        run_id=rid, goal=goal,
        timeline_a=timeline_a, timeline_b=timeline_b,
        duration_a=val_a.duration_s, duration_b=val_b.duration_s,
        events_a=events_a, events_b=events_b,
        insights_a=synth.insights_a, insights_b=synth.insights_b,
        aggregate_metrics=synth.aggregate_metrics,
        verdict=synth.verdict,
        elapsed_ms=elapsed_ms,
    )

    log.info(
        "analysis done",
        extra={**log_extra, "elapsed_ms": round(elapsed_ms, 2),
               "winner": synth.verdict.winner},
    )
    return response


class OrchestratorError(RuntimeError):
    """Validation / orchestration failure that maps to a 4xx response."""

    def __init__(self, *, field: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.field = field
        self.status_code = status_code
