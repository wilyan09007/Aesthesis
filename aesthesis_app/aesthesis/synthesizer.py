"""Insight Synthesizer (DESIGN.md §4.5).

Two Gemini calls per A/B comparison:
    1. Per-video insight call — Events + screenshots + goal -> insights JSON.
    2. Verdict call — Aggregate metrics + per-version insights -> Verdict JSON.

Failures from Gemini (missing dependency, missing key, malformed JSON,
schema mismatch) raise `SynthesizerError`. The orchestrator surfaces these
as a 500 to the caller.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import AppConfig
from .prompts import INSIGHT_PROMPT_TEMPLATE, VERDICT_PROMPT_TEMPLATE
from .schemas import AggregateMetric, Event, Insight, Verdict, VersionTag

log = logging.getLogger(__name__)


class SynthesizerError(RuntimeError):
    """Raised when a Gemini call cannot be completed or its output is unusable."""


@dataclass
class SynthesisResult:
    insights_a: list[Insight]
    insights_b: list[Insight]
    aggregate_metrics: list[AggregateMetric]
    verdict: Verdict
    insights_calls_ms: float
    verdict_call_ms: float


def _strip_code_fence(text: str) -> str:
    """Gemini in JSON mode usually returns raw JSON, but in `text` response
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

    `images` is a list of raw JPEG bytes. We pass them as inline image
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


async def _generate_insights(
    events: list[Event],
    *,
    version: VersionTag,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
) -> list[Insight]:
    """Call Gemini once per video. Returns list[Insight].

    Raises `SynthesizerError` if the call fails or produces zero valid
    insights for a non-empty event list.
    """
    if not events:
        log.info("no events to synthesize — skipping Gemini",
                 extra={"step": "gemini.insights", "version": version, "run_id": run_id})
        return []

    events_serialized = [_serialize_event_for_prompt(e) for e in events]
    events_json = json.dumps(events_serialized, indent=2)
    prompt = INSIGHT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        n_events=len(events),
        version=version,
        events_json=events_json,
    )

    images: list[bytes] = []
    for e in events:
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
            out.append(Insight(version=version, **{
                k: v for k, v in entry.items() if k != "version"
            }))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{entry}: {e}")
    if not out:
        raise SynthesizerError(
            f"Gemini insight call (version={version}) returned 0 valid "
            f"insights from {len(raw.get('insights', []))} entries. "
            f"errors={errors}"
        )
    return out


def _compute_aggregate_metrics(
    timeline_a: dict, timeline_b: dict,
) -> list[AggregateMetric]:
    """Compute the head-to-head metrics that go into the verdict prompt
    AND show up in the results-page JSON.

    Mirrors the table in DESIGN.md §4.5 step 3.
    """
    out: list[AggregateMetric] = []

    def _series(timeline: dict, key: str) -> list[float]:
        return [f.get("composites", {}).get(key, 0.0) for f in timeline.get("frames", [])]

    def _roi(timeline: dict, key: str) -> list[float]:
        return [f.get("values", {}).get(key, 0.0) for f in timeline.get("frames", [])]

    def _mean(xs: Sequence[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _emit(name: str, a: float, b: float, *, higher_is_better: bool = True,
              edge_description: str | None = None) -> None:
        if abs(a - b) < 1e-3:
            edge: str = "tie"
        elif higher_is_better:
            edge = "A" if a > b else "B"
        else:
            edge = "A" if a < b else "B"
        out.append(AggregateMetric(
            name=name, a=round(a, 4), b=round(b, 4),
            edge=edge,
            edge_description=edge_description or ("higher is better" if higher_is_better else None),
        ))

    a_appeal = _series(timeline_a, "appeal_index")
    b_appeal = _series(timeline_b, "appeal_index")
    _emit("mean_appeal_index", _mean(a_appeal), _mean(b_appeal))

    a_load = _roi(timeline_a, "cognitive_load")
    b_load = _roi(timeline_b, "cognitive_load")
    _emit("mean_cognitive_load", _mean(a_load), _mean(b_load),
          higher_is_better=False, edge_description="lower is better")

    def _pct_dominance(timeline: dict, roi: str) -> float:
        n = len(timeline.get("frames", []))
        if n == 0:
            return 0.0
        return 100.0 * sum(1 for f in timeline["frames"]
                           if f.get("dominant") == roi) / n

    _emit("pct_reward_dominance",
          _pct_dominance(timeline_a, "reward_anticipation"),
          _pct_dominance(timeline_b, "reward_anticipation"))

    _emit("pct_friction_dominance",
          _pct_dominance(timeline_a, "friction_anxiety"),
          _pct_dominance(timeline_b, "friction_anxiety"),
          higher_is_better=False, edge_description="lower is better")

    def _spike_count(timeline: dict, roi: str) -> int:
        return sum(1 for f in timeline.get("frames", [])
                   if f.get("spikes", {}).get(roi))

    _emit("friction_spike_count",
          float(_spike_count(timeline_a, "friction_anxiety")),
          float(_spike_count(timeline_b, "friction_anxiety")),
          higher_is_better=False, edge_description="fewer is better")

    a_motor = _roi(timeline_a, "motor_readiness")
    b_motor = _roi(timeline_b, "motor_readiness")
    _emit("motor_readiness_peak",
          max(a_motor) if a_motor else 0.0,
          max(b_motor) if b_motor else 0.0)

    def _window_count(timeline: dict, key: str) -> int:
        return sum(1 for w in timeline.get("windows", [])
                   if w.get("composites", {}).get(key))

    _emit("flow_state_windows",
          float(_window_count(timeline_a, "flow_state")),
          float(_window_count(timeline_b, "flow_state")))

    _emit("bounce_risk_windows",
          float(_window_count(timeline_a, "bounce_risk")),
          float(_window_count(timeline_b, "bounce_risk")),
          higher_is_better=False, edge_description="fewer is better")

    return out


async def _generate_verdict(
    metrics: list[AggregateMetric],
    insights_a: list[Insight],
    insights_b: list[Insight],
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
) -> Verdict:
    metrics_block = json.dumps([m.model_dump() for m in metrics], indent=2)
    insights_a_json = json.dumps([i.model_dump() for i in insights_a], indent=2)
    insights_b_json = json.dumps([i.model_dump() for i in insights_b], indent=2)
    prompt = VERDICT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        metrics_table_json=metrics_block,
        insights_a_json=insights_a_json,
        insights_b_json=insights_b_json,
    )
    raw = await _call_gemini(
        prompt, cfg=cfg, model_name=cfg.gemini_model_verdict,
        run_id=run_id, step="gemini.verdict",
    )
    try:
        return Verdict(**raw)
    except Exception as e:
        raise SynthesizerError(
            f"Gemini verdict returned an unparseable Verdict object: {e}"
        ) from e


async def synthesize(
    events_a: list[Event],
    events_b: list[Event],
    timeline_a: dict,
    timeline_b: dict,
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
) -> SynthesisResult:
    """Run the full synthesizer: per-video insights, then the verdict."""
    import time

    log.info(
        "synthesize begin",
        extra={"step": "synth", "run_id": run_id,
               "n_events_a": len(events_a), "n_events_b": len(events_b)},
    )

    t0 = time.perf_counter()
    insights_a = await _generate_insights(
        events_a, version="A", goal=goal, cfg=cfg, run_id=run_id,
    )
    insights_b = await _generate_insights(
        events_b, version="B", goal=goal, cfg=cfg, run_id=run_id,
    )
    insights_ms = (time.perf_counter() - t0) * 1000.0

    metrics = _compute_aggregate_metrics(timeline_a, timeline_b)

    t1 = time.perf_counter()
    verdict = await _generate_verdict(
        metrics, insights_a, insights_b, goal=goal, cfg=cfg, run_id=run_id,
    )
    verdict_ms = (time.perf_counter() - t1) * 1000.0

    log.info(
        "synthesize done",
        extra={"step": "synth", "run_id": run_id,
               "n_insights_a": len(insights_a), "n_insights_b": len(insights_b),
               "winner": verdict.winner, "elapsed_ms": round(insights_ms + verdict_ms, 2)},
    )
    return SynthesisResult(
        insights_a=insights_a,
        insights_b=insights_b,
        aggregate_metrics=metrics,
        verdict=verdict,
        insights_calls_ms=round(insights_ms, 2),
        verdict_call_ms=round(verdict_ms, 2),
    )
