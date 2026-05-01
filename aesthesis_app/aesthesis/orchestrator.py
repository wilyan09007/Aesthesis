"""End-to-end orchestrator for /api/analyze.

Sequence (DESIGN.md §3 / §17 — single-video pipeline):
    1. validate the MP4
    2. POST video to TRIBE /process_video_timeline (await ~3-8s)
    3. extract events (deterministic, ~10ms)
    4. extract per-event screenshots
    5. Gemini insight call (await ~1-3s)
    6. compute absolute aggregate metrics
    7. Gemini overall-assessment call (await ~1-2s)
    8. assemble AnalyzeResponse via output_builder

Total wall time: ~6-13s for a 30s clip. Verbose log lines emit at every
boundary so a slow run is easy to diagnose.

Pre-pivot this took ``video_a`` + ``video_b`` and ran TRIBE serially
(D6) on both. The pivot to single-video collapsed it — see DESIGN.md §17.
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
from .validation import validate_upload

log = logging.getLogger(__name__)


async def _attach_screenshots(
    events: list[Event],
    video: Path,
    *,
    work_dir: Path,
    run_id: str,
) -> None:
    """Mutates ``events`` in place — attaches ``.screenshot_path`` for every
    event whose timestamp we can extract a frame for.

    Each event spawns its own ffmpeg invocation; on Modal's 2-CPU container
    that's still cheap enough to fan out across the 15-event cap because
    ffmpeg with ``-ss`` before ``-i`` is keyframe-seek (sub-second). Running
    sequentially used to add ~30s on bad runs (failures stacking the 2s
    subprocess timeout). Fanning out drops it to roughly the worst single
    extraction.

    Emits a summary log line at the end so the per-run success rate is
    visible at INFO without grepping the noisy per-event traces. Gemini
    grounding quality drops sharply when many events lose their image
    so this is a load-bearing observability hook, not just nice-to-have.
    """
    if not events:
        return
    work_dir.mkdir(parents=True, exist_ok=True)
    log.debug(
        "extracting screenshots",
        extra={"step": "screenshots", "run_id": run_id, "n_events": len(events)},
    )

    t0 = time.perf_counter()

    def _extract(e: Event) -> None:
        out = work_dir / f"t{e.timestamp_s:.2f}.jpg"
        path = extract_frame(video, e.timestamp_s, out)
        if path is not None:
            e.screenshot_path = str(path)

    async def _extract_one(e: Event) -> None:
        try:
            await asyncio.to_thread(_extract, e)
        except Exception as ex:  # noqa: BLE001
            log.debug("screenshot failed at t=%.2f: %s", e.timestamp_s, ex)

    await asyncio.gather(*(_extract_one(e) for e in events))

    n_ok = sum(1 for e in events if e.screenshot_path)
    total_bytes = sum(
        Path(e.screenshot_path).stat().st_size
        for e in events
        if e.screenshot_path and Path(e.screenshot_path).exists()
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    log.info(
        "screenshots: %d/%d events grounded (%d bytes total, %.1fms)",
        n_ok, len(events), total_bytes, elapsed_ms,
        extra={"step": "screenshots", "run_id": run_id,
               "n_ok": n_ok, "n_events": len(events),
               "bytes": total_bytes, "elapsed_ms": elapsed_ms},
    )


async def run_analysis(
    *,
    cfg: AppConfig,
    video: Path,
    goal: str | None = None,
    run_id: str | None = None,
) -> AnalyzeResponse:
    rid = run_id or str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "orchestrator"}
    log.info(
        "analysis begin",
        extra={**log_extra, "video": str(video),
               "goal": goal, "tribe_url": cfg.tribe_service_url},
    )

    overall_t0 = time.perf_counter()

    # ── Step 1: validate ────────────────────────────────────────────────
    val = await asyncio.to_thread(validate_upload, video, cfg)
    if not val.ok:
        raise OrchestratorError(field="video", message=val.error or "invalid")
    log.info(
        "video validated",
        extra={**log_extra, "duration_s": val.duration_s},
    )

    # ── Step 2: TRIBE ────────────────────────────────────────────────────
    client = TribeClient(cfg.tribe_service_url, timeout_s=cfg.tribe_request_timeout_s)
    log.info("posting to TRIBE", extra=log_extra)
    timeline = await client.process_video_timeline(video, run_id=rid)

    # ── Step 3: events ──────────────────────────────────────────────────
    events = extract_events(timeline)

    # ── Step 4: screenshots ─────────────────────────────────────────────
    work_dir = cfg.upload_dir / rid / "frames"
    await _attach_screenshots(events, video, work_dir=work_dir, run_id=rid)

    # ── Steps 5-7: Gemini (insights + overall assessment) ───────────────
    synth = await synthesize(
        events, timeline,
        goal=goal, cfg=cfg, run_id=rid,
    )

    elapsed_ms = (time.perf_counter() - overall_t0) * 1000.0

    response = build_response(
        run_id=rid, goal=goal,
        timeline=timeline,
        duration_s=val.duration_s,
        events=events,
        insights=synth.insights,
        aggregate_metrics=synth.aggregate_metrics,
        overall_assessment=synth.overall_assessment,
        elapsed_ms=elapsed_ms,
    )

    log.info(
        "analysis done",
        extra={**log_extra, "elapsed_ms": round(elapsed_ms, 2),
               "n_events": len(events), "n_insights": len(synth.insights)},
    )
    return response


class OrchestratorError(RuntimeError):
    """Validation / orchestration failure that maps to a 4xx response."""

    def __init__(self, *, field: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.field = field
        self.status_code = status_code
