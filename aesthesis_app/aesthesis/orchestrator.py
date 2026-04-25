"""End-to-end orchestrator for /api/analyze.

Sequence (DESIGN.md §3 / §17 — single-video pipeline):
    1. validate the MP4
    2. POST video to TRIBE /process_video_timeline (await ~3-8s)
    3. extract events (deterministic, ~10ms)
    4. attach per-event context: screenshots + (D15) optional agent action stamps
    5. Gemini insight call (await ~1-3s)
    6. compute absolute aggregate metrics
    7. Gemini overall-assessment call (await ~1-2s)
    8. assemble AnalyzeResponse via output_builder

Total wall time: ~6-13s for a 30s clip. Verbose log lines emit at every
boundary so a slow run is easy to diagnose.

Pre-pivot this took ``video_a`` + ``video_b`` and ran TRIBE serially
(D6) on both. The pivot to single-video collapsed it — see DESIGN.md §17.

D15: when ``action_log_path`` is provided (capture path), each event's
``agent_action_at_t`` is stamped with the nearest BrowserUse action
within ±0.5s of the event timestamp. Skip-path callers pass None and
nothing changes vs v1.
"""

from __future__ import annotations

import asyncio
import json
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

# D15 — match window for action <-> event timestamp alignment
_ACTION_MATCH_WINDOW_S = 0.5


def _load_action_log(path: Path | None, *, run_id: str) -> list[dict]:
    """Load the BrowserUse actions.jsonl produced by the capture subprocess.

    Returns a list of dicts each containing ``timestamp_s`` (float) and
    ``description`` (str). Skips malformed lines with a warning. If the
    path is None or missing, returns []. Never raises — capture-path
    callers should still get analysis even if the action log is corrupt.
    """
    if path is None:
        log.debug(
            "action_log: not provided (skip path)",
            extra={"step": "actions", "run_id": run_id},
        )
        return []
    if not path.exists():
        log.warning(
            "action_log: path provided but file not found",
            extra={"step": "actions", "run_id": run_id, "path": str(path)},
        )
        return []

    actions: list[dict] = []
    n_skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = float(entry.get("timestamp_s", 0.0))
            desc = str(entry.get("description", "")).strip()
            if desc:
                actions.append({"timestamp_s": ts, "description": desc})
            else:
                n_skipped += 1
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            n_skipped += 1
            log.debug("action_log: skipping malformed line: %s", e,
                      extra={"step": "actions", "run_id": run_id})

    actions.sort(key=lambda a: a["timestamp_s"])
    log.info(
        "action_log: loaded",
        extra={"step": "actions", "run_id": run_id,
               "path": str(path), "n_actions": len(actions),
               "n_skipped": n_skipped},
    )
    return actions


def _nearest_action(actions: list[dict], t_s: float) -> dict | None:
    """Find the action whose timestamp is closest to ``t_s`` within
    the ±_ACTION_MATCH_WINDOW_S window. None if no action is close enough.

    Linear scan — actions are short (max ~30 per 30s capture).
    """
    if not actions:
        return None
    best = None
    best_dt = _ACTION_MATCH_WINDOW_S + 1.0
    for a in actions:
        dt = abs(a["timestamp_s"] - t_s)
        if dt <= _ACTION_MATCH_WINDOW_S and dt < best_dt:
            best = a
            best_dt = dt
    return best


async def _attach_per_event_context(
    events: list[Event],
    video: Path,
    *,
    work_dir: Path,
    run_id: str,
    action_log_path: Path | None = None,
) -> None:
    """Mutate ``events`` in place — attach ``.screenshot_path`` and (D15)
    ``.agent_action_at_t`` for every event we have evidence for.

    Function was previously named ``_attach_screenshots`` and took only
    the screenshot args. Renamed + extended to carry the action_log
    plumbing without changing the screenshot semantics for the skip path.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    log.debug(
        "attach_per_event_context: begin",
        extra={"step": "context", "run_id": run_id,
               "n_events": len(events),
               "has_action_log": action_log_path is not None},
    )

    actions = _load_action_log(action_log_path, run_id=run_id)

    def _extract(e: Event) -> None:
        out = work_dir / f"t{e.timestamp_s:.2f}.jpg"
        path = extract_frame(video, e.timestamp_s, out)
        if path is not None:
            e.screenshot_path = str(path)

    n_actions_stamped = 0
    for e in events:
        try:
            _extract(e)
        except Exception as ex:  # noqa: BLE001
            log.debug("screenshot failed at t=%.2f: %s", e.timestamp_s, ex,
                      extra={"step": "context", "run_id": run_id})
        # D15 — stamp action_at_t when we have an action log + a near match
        match = _nearest_action(actions, e.timestamp_s)
        if match is not None:
            e.agent_action_at_t = match["description"][:500]
            n_actions_stamped += 1

    log.info(
        "attach_per_event_context: done",
        extra={"step": "context", "run_id": run_id,
               "n_events": len(events),
               "n_actions_stamped": n_actions_stamped,
               "n_actions_loaded": len(actions)},
    )


async def run_analysis(
    *,
    cfg: AppConfig,
    video: Path,
    goal: str | None = None,
    run_id: str | None = None,
    action_log_path: Path | None = None,
) -> AnalyzeResponse:
    rid = run_id or str(uuid.uuid4())
    log_extra = {"run_id": rid, "step": "orchestrator"}
    log.info(
        "analysis begin",
        extra={**log_extra, "video": str(video),
               "goal": goal, "tribe_url": cfg.tribe_service_url,
               "has_action_log": action_log_path is not None},
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

    # ── Step 4: per-event context (screenshots + optional action stamps) ─
    work_dir = cfg.upload_dir / rid / "frames"
    await _attach_per_event_context(
        events, video,
        work_dir=work_dir, run_id=rid,
        action_log_path=action_log_path,
    )

    # ── Steps 5-7: Gemini (insights + overall assessment) ───────────────
    synth = await synthesize(
        events, timeline,
        goal=goal, cfg=cfg, run_id=rid,
        duration_s=val.duration_s,
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
