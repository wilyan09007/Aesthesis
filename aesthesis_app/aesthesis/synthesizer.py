"""Insight Synthesizer (DESIGN.md §4.5, post-pivot §17).

Two Gemini calls per analysis:
    1. Per-event insights — Events + screenshots + goal -> insights JSON.
    2. Overall assessment — Aggregate metrics + insights -> OverallAssessment.

Failures from Gemini (missing dependency, missing key, malformed JSON,
schema mismatch) raise ``SynthesizerError``. The orchestrator surfaces these
as a 500 to the caller.

Pre-pivot history: there used to be three calls (insights for A, insights
for B, then a verdict that picked a winner). The pivot collapsed that to
two single-subject calls — DESIGN.md §17.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import AppConfig
from .prompts import ASSESSMENT_PROMPT_TEMPLATE, INSIGHT_PROMPT_TEMPLATE
from .schemas import AggregateMetric, Event, Insight, OverallAssessment

log = logging.getLogger(__name__)


class SynthesizerError(RuntimeError):
    """Raised when a Gemini call cannot be completed or its output is unusable."""


@dataclass
class SynthesisResult:
    insights: list[Insight]
    aggregate_metrics: list[AggregateMetric]
    overall_assessment: OverallAssessment
    insights_call_ms: float
    assessment_call_ms: float


# ─── Gemini plumbing ─────────────────────────────────────────────────────────

def _strip_code_fence(text: str) -> str:
    """Gemini in JSON mode usually returns raw JSON, but in ``text`` response
    mode it sometimes wraps the body in ```json ... ```. Trim that."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


async def _call_gemini(prompt: str, *, cfg: AppConfig, model_name: str,
                       run_id: str, step: str,
                       images: list[bytes] | None = None) -> dict:
    """Invoke Gemini and return the parsed JSON response.

    ``images`` is a list of raw JPEG bytes. We pass them as inline image
    parts alongside the text prompt so Gemini can ground insights in the
    actual UI screenshots.
    """
    if not cfg.gemini_api_key:
        raise SynthesizerError("GEMINI_API_KEY is not set")

    try:
        import google.generativeai as genai  # type: ignore
    except ImportError as e:
        raise SynthesizerError(
            "google-generativeai not installed. "
            "Install with `pip install google-generativeai`."
        ) from e

    genai.configure(api_key=cfg.gemini_api_key)

    parts: list = [prompt]
    if images:
        for img in images:
            parts.append({"mime_type": "image/jpeg", "data": img})

    log.info(
        "Gemini call",
        extra={"step": step, "run_id": run_id,
               "model": model_name, "n_images": len(images or [])},
    )
    model = genai.GenerativeModel(model_name)
    resp = await model.generate_content_async(
        parts,
        generation_config={"response_mime_type": "application/json",
                           "temperature": 0.2},
    )
    text = _strip_code_fence(resp.text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SynthesizerError(
            f"Gemini returned malformed JSON (step={step}): {e}"
        ) from e


def _serialize_event_for_prompt(e: Event) -> dict:
    return {
        "timestamp_s": e.timestamp_s,
        "type": e.type,
        "primary_roi": e.primary_roi,
        "magnitude": round(e.magnitude, 4),
        "co_events": e.co_events,
        "agent_action_at_t": e.agent_action_at_t,
    }


# ─── Step 1: per-event insights ──────────────────────────────────────────────

def _clamp_insight_range(
    insight: Insight, duration_s: float, run_id: str,
) -> Insight | None:
    """Snap ``timestamp_range_s`` into [0, duration_s]. Drop insights whose
    start is already past the video — those are pure hallucinations.

    Gemini occasionally drifts outside the clip's bounds despite the prompt
    constraint (DESIGN.md §4.5: ranges must lie inside the video). We
    enforce it deterministically here so the frontend never sees a card
    pointing past the end of the player timeline.
    """
    start, end = insight.timestamp_range_s
    if start >= duration_s:
        log.warning(
            "dropping insight whose start is past video end",
            extra={"step": "gemini.insights", "run_id": run_id,
                   "start_s": start, "end_s": end, "duration_s": duration_s},
        )
        return None
    new_start = max(0.0, min(start, duration_s))
    new_end = max(new_start, min(end, duration_s))
    if (new_start, new_end) == (start, end):
        return insight
    log.info(
        "clamped insight timestamp_range_s to video bounds",
        extra={"step": "gemini.insights", "run_id": run_id,
               "from": [start, end], "to": [new_start, new_end],
               "duration_s": duration_s},
    )
    return insight.model_copy(update={"timestamp_range_s": (new_start, new_end)})


async def _generate_insights(
    events: list[Event],
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
    duration_s: float,
) -> list[Insight]:
    """Call Gemini once for the demo's events. Returns list[Insight].

    Raises ``SynthesizerError`` if the call fails or produces zero valid
    insights for a non-empty event list.
    """
    if not events:
        log.info("no events to synthesize — skipping Gemini",
                 extra={"step": "gemini.insights", "run_id": run_id})
        return []

    # Drop events whose timestamp is past the actual video. TRIBE produces
    # one frame per TR (1.5s); on a video whose duration isn't a clean
    # multiple of TR, the last frame can sit slightly past duration_s and
    # then mislead Gemini into emitting an out-of-range range.
    bounded = [e for e in events if e.timestamp_s < duration_s]
    if len(bounded) != len(events):
        log.info(
            "filtered events past video duration before Gemini",
            extra={"step": "gemini.insights", "run_id": run_id,
                   "n_in": len(events), "n_kept": len(bounded),
                   "duration_s": duration_s},
        )
    if not bounded:
        return []

    events_serialized = [_serialize_event_for_prompt(e) for e in bounded]
    events_json = json.dumps(events_serialized, indent=2)
    prompt = INSIGHT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        n_events=len(bounded),
        duration_s=duration_s,
        events_json=events_json,
    )

    images: list[bytes] = []
    for e in bounded:
        if e.screenshot_path and Path(e.screenshot_path).exists():
            try:
                images.append(Path(e.screenshot_path).read_bytes())
            except OSError:
                continue

    raw = await _call_gemini(
        prompt, cfg=cfg, model_name=cfg.gemini_model_insights,
        run_id=run_id, step="gemini.insights",
        images=images or None,
    )

    out: list[Insight] = []
    errors: list[str] = []
    for entry in raw.get("insights", []):
        try:
            parsed = Insight(**entry)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{entry}: {e}")
            continue
        clamped = _clamp_insight_range(parsed, duration_s, run_id)
        if clamped is not None:
            out.append(clamped)
    if not out:
        raise SynthesizerError(
            f"Gemini insight call returned 0 valid insights from "
            f"{len(raw.get('insights', []))} entries. errors={errors}"
        )
    return out


# ─── Step 2: absolute aggregate metrics ──────────────────────────────────────

def _compute_aggregate_metrics(timeline: dict) -> list[AggregateMetric]:
    """Eight absolute metrics scored against this demo's own timeline.

    Mirrors the table in DESIGN.md §4.5 step 3 but reframed for single-video:
    every metric is a self-contained ``(name, value, interpretation)``,
    no A vs B comparison.

    Roughly:
      mean_appeal_index        — z-scored, near 0 means neutral arc
      mean_cognitive_load      — z-scored, positive = sustained load
      pct_reward_dominance     — % of TRs where reward_anticipation was the
                                 dominant ROI
      pct_friction_dominance   — % of TRs where friction_anxiety dominated
      friction_spike_count     — count of TRs with a friction spike
      motor_readiness_peak     — max raw motor_readiness over the demo
      flow_state_windows       — count of sliding windows hitting flow
      bounce_risk_windows      — count of sliding windows hitting bounce-risk
    """
    def _series(timeline: dict, key: str) -> list[float]:
        return [f.get("composites", {}).get(key, 0.0) for f in timeline.get("frames", [])]

    def _roi(timeline: dict, key: str) -> list[float]:
        return [f.get("values", {}).get(key, 0.0) for f in timeline.get("frames", [])]

    def _mean(xs: Sequence[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _pct_dominance(timeline: dict, roi: str) -> float:
        n = len(timeline.get("frames", []))
        if n == 0:
            return 0.0
        return 100.0 * sum(
            1 for f in timeline["frames"] if f.get("dominant") == roi
        ) / n

    def _spike_count(timeline: dict, roi: str) -> int:
        return sum(
            1 for f in timeline.get("frames", [])
            if f.get("spikes", {}).get(roi)
        )

    def _window_count(timeline: dict, key: str) -> int:
        return sum(
            1 for w in timeline.get("windows", [])
            if w.get("composites", {}).get(key)
        )

    appeal = _mean(_series(timeline, "appeal_index"))
    load = _mean(_roi(timeline, "cognitive_load"))
    reward_pct = _pct_dominance(timeline, "reward_anticipation")
    friction_pct = _pct_dominance(timeline, "friction_anxiety")
    friction_spikes = _spike_count(timeline, "friction_anxiety")
    motor = _roi(timeline, "motor_readiness")
    motor_peak = max(motor) if motor else 0.0
    flow_windows = _window_count(timeline, "flow_state")
    bounce_windows = _window_count(timeline, "bounce_risk")

    def _appeal_phrase(v: float) -> str:
        if v > 0.15:
            return "appeal arc skewed positive"
        if v < -0.15:
            return "appeal arc skewed negative"
        return "appeal arc broadly neutral"

    def _load_phrase(v: float) -> str:
        if v > 0.15:
            return "sustained cognitive load above baseline"
        if v < -0.15:
            return "easy reading — load below baseline"
        return "load near baseline"

    return [
        AggregateMetric(
            name="mean_appeal_index", value=round(appeal, 4),
            interpretation=_appeal_phrase(appeal),
        ),
        AggregateMetric(
            name="mean_cognitive_load", value=round(load, 4),
            interpretation=_load_phrase(load),
        ),
        AggregateMetric(
            name="pct_reward_dominance", value=round(reward_pct, 2),
            interpretation=f"{reward_pct:.0f}% of the demo had reward dominant",
        ),
        AggregateMetric(
            name="pct_friction_dominance", value=round(friction_pct, 2),
            interpretation=f"{friction_pct:.0f}% of the demo had friction dominant",
        ),
        AggregateMetric(
            name="friction_spike_count", value=float(friction_spikes),
            interpretation=f"{friction_spikes} friction spike(s) detected",
        ),
        AggregateMetric(
            name="motor_readiness_peak", value=round(motor_peak, 4),
            interpretation="peak click-readiness over the demo",
        ),
        AggregateMetric(
            name="flow_state_windows", value=float(flow_windows),
            interpretation=f"{flow_windows} flow-state window(s)",
        ),
        AggregateMetric(
            name="bounce_risk_windows", value=float(bounce_windows),
            interpretation=f"{bounce_windows} bounce-risk window(s)",
        ),
    ]


# ─── Step 3: overall assessment ──────────────────────────────────────────────

async def _generate_overall_assessment(
    metrics: list[AggregateMetric],
    insights: list[Insight],
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
    duration_s: float,
) -> OverallAssessment:
    metrics_block = json.dumps([m.model_dump() for m in metrics], indent=2)
    insights_json = json.dumps([i.model_dump() for i in insights], indent=2)
    prompt = ASSESSMENT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        duration_s=duration_s,
        metrics_table_json=metrics_block,
        insights_json=insights_json,
    )
    raw = await _call_gemini(
        prompt, cfg=cfg, model_name=cfg.gemini_model_verdict,
        run_id=run_id, step="gemini.assessment",
    )
    try:
        return OverallAssessment(**raw)
    except Exception as e:
        raise SynthesizerError(
            f"Gemini assessment returned an unparseable OverallAssessment: {e}"
        ) from e


# ─── Top-level entry point ───────────────────────────────────────────────────

async def synthesize(
    events: list[Event],
    timeline: dict,
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
    duration_s: float,
) -> SynthesisResult:
    """Run the full synthesizer: per-event insights, then overall assessment.

    ``duration_s`` is the validated MP4 duration. We thread it into both
    Gemini prompts and clamp returned ``timestamp_range_s`` values to
    [0, duration_s] so card timestamps cannot exceed the video.
    """
    log.info(
        "synthesize begin",
        extra={"step": "synth", "run_id": run_id, "n_events": len(events),
               "duration_s": duration_s},
    )

    t0 = time.perf_counter()
    insights = await _generate_insights(
        events, goal=goal, cfg=cfg, run_id=run_id, duration_s=duration_s,
    )
    insights_ms = (time.perf_counter() - t0) * 1000.0

    metrics = _compute_aggregate_metrics(timeline)

    t1 = time.perf_counter()
    overall = await _generate_overall_assessment(
        metrics, insights, goal=goal, cfg=cfg, run_id=run_id,
        duration_s=duration_s,
    )
    assessment_ms = (time.perf_counter() - t1) * 1000.0

    log.info(
        "synthesize done",
        extra={"step": "synth", "run_id": run_id,
               "n_insights": len(insights),
               "elapsed_ms": round(insights_ms + assessment_ms, 2)},
    )
    return SynthesisResult(
        insights=insights,
        aggregate_metrics=metrics,
        overall_assessment=overall,
        insights_call_ms=round(insights_ms, 2),
        assessment_call_ms=round(assessment_ms, 2),
    )
