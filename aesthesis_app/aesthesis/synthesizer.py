"""Insight Synthesizer (DESIGN.md §4.5, post-pivot §17,
agent-prompt restructure ASSUMPTIONS_AGENT_PROMPT.md).

Two Gemini calls per analysis:
    1. Per-event structured insights — Events + screenshots + goal ->
       Insight JSON with target_element + proposed_change +
       acceptance_criteria + confidence, per ASSUMPTIONS_AGENT_PROMPT.md
       §3-§5.
    2. Overall assessment — Aggregate metrics + insights ->
       OverallAssessment.

Between the two calls, every insight is enriched server-side:
  - Annotated screenshot generated via PIL bbox overlay (annotate.py),
  - Final paste-into-coding-agent Markdown rendered deterministically
    via the prompt_renderer module.

Both enrichment steps are CPU-only and degradable: a per-event failure
drops the overlay or the prompt for that one insight rather than
failing the whole analysis. Verbose logging at every boundary —
agent-prompt quality is the headline product feature, observability
needs to surface failures without grepping per-event debug noise.

Failures from Gemini (missing dependency, missing key, malformed JSON,
schema mismatch) raise ``SynthesizerError``. The orchestrator surfaces
these as a 500 to the caller.

Pre-pivot history: there used to be three calls (insights for A,
insights for B, then a verdict that picked a winner). The pivot
collapsed that to two single-subject calls — DESIGN.md §17.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .annotate import annotate_to_b64_jpeg
from .config import AppConfig
from .prompt_renderer import render_agent_prompt
from .prompts import ASSESSMENT_PROMPT_TEMPLATE, INSIGHT_PROMPT_TEMPLATE
from .schemas import (
    AggregateMetric,
    Event,
    Insight,
    OverallAssessment,
    ProposedChange,
    TargetElement,
)

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
                       images: list[bytes] | None = None) -> Any:
    """Invoke Gemini and return the parsed JSON response.

    ``images`` is a list of raw JPEG bytes. We pass them as inline image
    parts alongside the text prompt so Gemini can ground insights in the
    actual UI screenshots.
    """
    if not cfg.gemini_api_key:
        raise SynthesizerError("GEMINI_API_KEY is not set")

    try:
        import google.generativeai as genai  # type: ignore
        from google.api_core import retry as _retries  # type: ignore
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
               "model": model_name, "n_images": len(images or []),
               "prompt_chars": len(prompt)},
    )
    t0 = time.perf_counter()
    model = genai.GenerativeModel(model_name)
    # Disable SDK-level retries — fail fast so an empty/blocked response
    # surfaces immediately instead of stalling the request for ~70s while
    # the api_core layer retries against the same failure mode.
    no_retry = _retries.AsyncRetry(predicate=lambda _exc: False)
    resp = await model.generate_content_async(
        parts,
        generation_config={"response_mime_type": "application/json",
                           "temperature": 0.2},
        request_options={"retry": no_retry},
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    # Defensive: resp.text can raise if the response was safety-blocked
    # (no Parts on the candidate). Surface that explicitly with the
    # finish_reason so we don't print a confusing JSONDecodeError.
    try:
        raw_text = resp.text
    except Exception as e:  # noqa: BLE001
        finish_reasons = []
        try:
            for cand in (resp.candidates or []):
                fr = getattr(cand, "finish_reason", None)
                if fr is not None:
                    finish_reasons.append(str(fr))
        except Exception:  # noqa: BLE001
            pass
        log.error(
            "Gemini response had no text — likely safety-blocked. finish_reasons=%s",
            finish_reasons,
            extra={"step": step, "run_id": run_id,
                   "elapsed_ms": elapsed_ms,
                   "finish_reasons": finish_reasons},
        )
        raise SynthesizerError(
            f"Gemini returned no text (finish_reasons={finish_reasons}): {e}"
        ) from e

    text = _strip_code_fence(raw_text)
    log.info(
        "Gemini response received",
        extra={"step": step, "run_id": run_id,
               "elapsed_ms": elapsed_ms,
               "response_chars": len(text),
               # Excerpt the head + tail so we can diagnose empty-response
               # / wrong-shape failures from production logs without
               # capturing the full body (which can include user copy).
               "response_head": text[:200],
               "response_tail": text[-200:] if len(text) > 200 else ""},
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.error(
            "Gemini JSON decode failed — body excerpt: %s",
            text[:600],
            extra={"step": step, "run_id": run_id},
        )
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


# ─── Step 1: per-event structured insights ──────────────────────────────────

def _coerce_target_element(raw: Any) -> TargetElement | None:
    """Defensive parse of Gemini's target_element block.

    Gemini occasionally drops a field or returns a string for a list. We
    salvage what we can and let the unclear-branch renderer take over
    when the structure is too damaged.
    """
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label", "")).strip()
    if not label:
        # No label means we can't even render the unclear branch
        # meaningfully — drop the structured part and let the renderer
        # fall back further.
        return None

    visual_anchors = raw.get("visual_anchors") or []
    if isinstance(visual_anchors, str):
        visual_anchors = [visual_anchors]
    visual_anchors = [str(a).strip() for a in visual_anchors if str(a).strip()]

    bbox = raw.get("bbox_norm")
    if bbox is not None:
        try:
            bbox = tuple(float(v) for v in bbox)
            if len(bbox) != 4:
                bbox = None
        except (TypeError, ValueError):
            bbox = None

    visible_text = raw.get("visible_text")
    if visible_text is not None:
        visible_text = str(visible_text).strip() or None

    return TargetElement(
        label=label,
        element_type=str(raw.get("element_type") or "element"),
        visible_text=visible_text,
        location_hint=str(raw.get("location_hint") or "").strip(),
        visual_anchors=visual_anchors,
        bbox_norm=bbox,
    )


def _coerce_proposed_change(raw: Any) -> ProposedChange | None:
    if not isinstance(raw, dict):
        return None
    current_state = str(raw.get("current_state", "")).strip()
    desired_state = str(raw.get("desired_state", "")).strip()
    rationale = str(raw.get("rationale", "")).strip()
    if not (current_state or desired_state):
        return None

    change_type = str(raw.get("change_type") or "structure").strip().lower()
    valid = {"copy", "layout", "hierarchy", "color", "spacing",
             "typography", "interaction", "removal", "addition", "structure"}
    if change_type not in valid:
        log.info(
            "Gemini returned unknown change_type=%r — coercing to 'structure'",
            change_type,
            extra={"step": "synth.coerce"},
        )
        change_type = "structure"

    return ProposedChange(
        change_type=change_type,  # type: ignore[arg-type]
        current_state=current_state,
        desired_state=desired_state or current_state,
        rationale=rationale,
    )


def _coerce_acceptance_criteria(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(c).strip() for c in raw if str(c).strip()]


def _coerce_confidence(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    if v > 1.0 and v <= 100.0:
        v = v / 100.0  # Gemini sometimes uses 0..100 instead of 0..1
    return max(0.0, min(1.0, v))


def _build_insight_from_raw(entry: dict, idx: int, run_id: str) -> Insight | None:
    """Translate one Gemini JSON entry into an Insight model.

    Returns ``None`` only when the event truly carries nothing
    actionable. Most failures degrade to the unclear-branch flow rather
    than dropping the insight entirely.
    """
    try:
        ts_range = entry["timestamp_range_s"]
        t0 = float(ts_range[0])
        t1 = float(ts_range[1])
    except (KeyError, TypeError, ValueError, IndexError):
        log.info(
            "insight %d dropped — missing/malformed timestamp_range_s: %r",
            idx, entry.get("timestamp_range_s"),
            extra={"step": "synth.parse", "run_id": run_id, "insight_idx": idx},
        )
        return None

    target = _coerce_target_element(entry.get("target_element"))
    change = _coerce_proposed_change(entry.get("proposed_change"))
    criteria = _coerce_acceptance_criteria(entry.get("acceptance_criteria"))
    confidence = _coerce_confidence(entry.get("confidence", 0.0))

    cited_features = entry.get("cited_brain_features") or []
    if isinstance(cited_features, str):
        cited_features = [cited_features]
    cited_features = [str(f).strip() for f in cited_features if str(f).strip()]

    recommendation = str(entry.get("recommendation") or "").strip()
    if not recommendation and change is not None:
        # Derive a one-line recommendation from the structured change
        # so the existing chart hover tooltip keeps working.
        recommendation = change.desired_state[:240]

    return Insight(
        timestamp_range_s=(t0, t1),
        ux_observation=str(entry.get("ux_observation", "")).strip(),
        recommendation=recommendation,
        cited_brain_features=cited_features,
        cited_screen_moment=str(entry.get("cited_screen_moment", "")).strip(),
        target_element=target,
        proposed_change=change,
        acceptance_criteria=criteria,
        confidence=confidence,
        agent_prompt="",  # filled in by render step below
        annotated_screenshot_b64=None,  # filled in by annotate step below
    )


def _degraded_insight_from_event(event: Event) -> Insight:
    """Last-resort fallback when Gemini returns nothing usable.

    Builds a minimal Insight from the event's raw fields. The renderer
    routes this through the unclear branch (confidence=0, no target),
    so the user gets a "investigate this moment" prompt rather than a
    fix instruction. This is strictly better than a 500 — the brain
    timeline + assessment + per-event timestamps + cited features are
    still real signal; only the agent-paste prompt degrades.

    Reason for the fallback path: Gemini in JSON mode occasionally
    returns an empty response or a wrong-shape body when the prompt
    is unusually strict. We log + recover instead of crashing.
    """
    roi = event.primary_roi or "an unspecified region"
    cited = [event.primary_roi] if event.primary_roi else []
    if event.type:
        # Carry the deterministic event type into cited features so the
        # unclear-branch prompt has at least one thing to anchor on.
        cited = list(dict.fromkeys([*cited, f"event_type:{event.type}"]))

    return Insight(
        timestamp_range_s=(event.timestamp_s, event.timestamp_s + 1.5),
        ux_observation=(
            f"At t={event.timestamp_s:.1f}s, a {event.type} event fired in "
            f"{roi}. The screenshot at this timestamp was not enough for "
            "the analysis pass to commit to a specific element — investigate "
            "the frame manually before applying any change."
        ),
        recommendation="Investigate this moment in the screen recording.",
        cited_brain_features=cited,
        cited_screen_moment=f"frame at t={event.timestamp_s:.1f}s",
        target_element=None,
        proposed_change=None,
        acceptance_criteria=[],
        confidence=0.0,
        agent_prompt="",  # filled by enrichment step (unclear-branch render)
        annotated_screenshot_b64=None,
    )


async def _generate_structured_insights(
    events: list[Event],
    *,
    goal: str | None,
    cfg: AppConfig,
    run_id: str,
) -> list[Insight]:
    """Call Gemini once for the demo's events. Returns list[Insight].

    Raises ``SynthesizerError`` if the call fails or produces zero valid
    insights for a non-empty event list.
    """
    if not events:
        log.info("no events to synthesize — skipping Gemini",
                 extra={"step": "synth.insights", "run_id": run_id})
        return []

    events_serialized = [_serialize_event_for_prompt(e) for e in events]
    events_json = json.dumps(events_serialized, indent=2)
    prompt = INSIGHT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        n_events=len(events),
        events_json=events_json,
    )

    images: list[bytes] = []
    for e in events:
        if e.screenshot_path and Path(e.screenshot_path).exists():
            try:
                images.append(Path(e.screenshot_path).read_bytes())
            except OSError as ex:
                log.info(
                    "could not read screenshot for t=%.2f: %s",
                    e.timestamp_s, ex,
                    extra={"step": "synth.insights", "run_id": run_id},
                )
                continue

    log.info(
        "synth: requesting %d structured insights (goal=%s, %d images attached)",
        len(events), bool(goal), len(images),
        extra={"step": "synth.insights", "run_id": run_id,
               "n_events": len(events), "n_images": len(images)},
    )

    raw = await _call_gemini(
        prompt, cfg=cfg, model_name=cfg.gemini_model_insights,
        run_id=run_id, step="synth.insights",
        images=images or None,
    )

    # Permissive parsing: Gemini in JSON mode is supposed to return our
    # exact `{"insights": [...]}` shape, but a long restrictive prompt
    # sometimes makes it return a top-level array, an alternate key,
    # or just `{}`. Try every reasonable shape before giving up.
    raw_insights: list = []
    raw_shape: str = "unknown"
    if isinstance(raw, list):
        raw_insights = raw
        raw_shape = "top_level_array"
    elif isinstance(raw, dict):
        # Canonical key first, then a handful of plausible alternates.
        for key in ("insights", "events", "results", "items", "data"):
            v = raw.get(key)
            if isinstance(v, list) and v:
                raw_insights = v
                raw_shape = f"dict[{key}]"
                break
        if not raw_insights:
            # Last-ditch: any list-valued field with object entries.
            for key, value in raw.items():
                if (isinstance(value, list) and value
                        and isinstance(value[0], dict)):
                    raw_insights = value
                    raw_shape = f"dict[{key}]_inferred"
                    log.info(
                        "synth: Gemini used unexpected key %r — accepting it",
                        key,
                        extra={"step": "synth.parse", "run_id": run_id},
                    )
                    break

    log.info(
        "Gemini returned %d raw insight entries (shape=%s)",
        len(raw_insights), raw_shape,
        extra={"step": "synth.insights", "run_id": run_id,
               "n_raw": len(raw_insights), "shape": raw_shape},
    )

    out: list[Insight] = []
    for idx, entry in enumerate(raw_insights):
        if not isinstance(entry, dict):
            log.info(
                "insight %d dropped — non-object entry: %r",
                idx, entry,
                extra={"step": "synth.parse", "run_id": run_id,
                       "insight_idx": idx},
            )
            continue
        ins = _build_insight_from_raw(entry, idx, run_id)
        if ins is not None:
            out.append(ins)

    if not out:
        # Degraded-mode fallback: Gemini either returned nothing usable or
        # the schema couldn't be parsed. Rather than 500, build a minimal
        # "unclear-branch" Insight per event — the user gets a results page
        # with timeline + assessment + per-event "investigate this moment"
        # prompts instead of a hard failure. The event payload alone is
        # enough to drive the unclear-branch renderer.
        log.warning(
            "synth: Gemini returned 0 usable insights for %d events "
            "(shape=%s) — falling back to degraded per-event placeholders",
            len(events), raw_shape,
            extra={"step": "synth.degraded", "run_id": run_id,
                   "n_events": len(events), "shape": raw_shape},
        )
        out = [_degraded_insight_from_event(e) for e in events]

    log.info(
        "synth: %d insights parsed (target_elements=%d, proposed_changes=%d, "
        "criteria_avg=%.1f, mean_confidence=%.2f)",
        len(out),
        sum(1 for i in out if i.target_element is not None),
        sum(1 for i in out if i.proposed_change is not None),
        (sum(len(i.acceptance_criteria) for i in out) / len(out)) if out else 0.0,
        (sum(i.confidence for i in out) / len(out)) if out else 0.0,
        extra={"step": "synth.insights", "run_id": run_id,
               "n_insights": len(out)},
    )

    return out


# ─── Step 1.5: enrichment — annotated screenshots + rendered prompts ────────

def _enrich_insights(
    insights: list[Insight],
    events: list[Event],
    *,
    goal: str | None,
    run_id: str,
) -> None:
    """Mutate insights in place, adding ``annotated_screenshot_b64`` and
    ``agent_prompt`` fields. CPU-only — runs synchronously off the event
    loop via ``asyncio.to_thread`` from the orchestrator.

    Per-insight failures are non-fatal — a missing overlay or render
    error degrades that one insight but never the whole run.
    """
    if not insights:
        return

    # Index events by approximate timestamp so we can locate the source
    # screenshot for an insight from its timestamp_range_s start.
    event_by_t: dict[float, Event] = {round(e.timestamp_s, 2): e for e in events}

    n_annotated = 0
    n_prompts = 0
    n_unclear = 0
    for idx, ins in enumerate(insights):
        # Find the source event's screenshot. If we can't, the prompt is
        # still rendered; only the annotated overlay is dropped.
        screenshot_path: Path | None = None
        if ins.target_element is not None and ins.target_element.bbox_norm is not None:
            t_key = round(ins.timestamp_range_s[0], 2)
            ev = event_by_t.get(t_key)
            if ev is None:
                # Fall back to nearest event
                ev = min(
                    events,
                    key=lambda e: abs(e.timestamp_s - ins.timestamp_range_s[0]),
                    default=None,
                )
            if ev is not None and ev.screenshot_path:
                p = Path(ev.screenshot_path)
                if p.exists():
                    screenshot_path = p

            if screenshot_path is not None:
                ins.annotated_screenshot_b64 = annotate_to_b64_jpeg(
                    screenshot_path,
                    ins.target_element.bbox_norm,
                    run_id=run_id,
                    insight_idx=idx,
                )
                if ins.annotated_screenshot_b64:
                    n_annotated += 1

        try:
            ins.agent_prompt = render_agent_prompt(ins, goal=goal)
            n_prompts += 1
            label = ins.target_element.label if ins.target_element else ""
            if (
                ins.confidence < 0.4
                or (label or "").strip().lower().startswith("unclear")
            ):
                n_unclear += 1
        except Exception as e:  # noqa: BLE001
            log.warning(
                "prompt render failed for insight %d (%s: %s) — leaving "
                "agent_prompt empty; frontend will hide the copy button.",
                idx, type(e).__name__, e,
                extra={"step": "synth.render", "run_id": run_id,
                       "insight_idx": idx},
            )

    log.info(
        "synth: enrichment done — %d/%d annotated, %d/%d prompts rendered, "
        "%d unclear-branch",
        n_annotated, len(insights), n_prompts, len(insights), n_unclear,
        extra={"step": "synth.enrich", "run_id": run_id,
               "n_annotated": n_annotated, "n_prompts": n_prompts,
               "n_unclear": n_unclear, "n_insights": len(insights)},
    )


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
) -> OverallAssessment:
    metrics_block = json.dumps([m.model_dump() for m in metrics], indent=2)
    # Strip the heavy fields (annotated_screenshot_b64, agent_prompt) so
    # the assessment prompt stays small. Those fields are for the
    # frontend, not for assessment grounding.
    insights_for_assessment = [
        {
            "timestamp_range_s": list(i.timestamp_range_s),
            "ux_observation": i.ux_observation,
            "recommendation": i.recommendation,
            "cited_brain_features": i.cited_brain_features,
            "cited_screen_moment": i.cited_screen_moment,
        }
        for i in insights
    ]
    insights_json = json.dumps(insights_for_assessment, indent=2)
    prompt = ASSESSMENT_PROMPT_TEMPLATE.format(
        goal=goal or "general first-impression evaluation",
        metrics_table_json=metrics_block,
        insights_json=insights_json,
    )
    raw = await _call_gemini(
        prompt, cfg=cfg, model_name=cfg.gemini_model_verdict,
        run_id=run_id, step="synth.assessment",
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
) -> SynthesisResult:
    """Run the full synthesizer: structured insights + enrichment + assessment."""
    log.info(
        "synthesize begin",
        extra={"step": "synth", "run_id": run_id, "n_events": len(events),
               "goal_present": goal is not None},
    )

    t0 = time.perf_counter()
    insights = await _generate_structured_insights(
        events, goal=goal, cfg=cfg, run_id=run_id,
    )
    insights_ms = (time.perf_counter() - t0) * 1000.0

    # CPU-only enrichment: bbox overlay + Markdown prompt rendering.
    # Runs inline on the asyncio thread — both are fast (<200ms total
    # for 15 insights at 1600px screenshots) and we want them sequenced
    # before the assessment prompt is built.
    t_enrich = time.perf_counter()
    _enrich_insights(insights, events, goal=goal, run_id=run_id)
    enrich_ms = (time.perf_counter() - t_enrich) * 1000.0

    metrics = _compute_aggregate_metrics(timeline)

    t1 = time.perf_counter()
    overall = await _generate_overall_assessment(
        metrics, insights, goal=goal, cfg=cfg, run_id=run_id,
    )
    assessment_ms = (time.perf_counter() - t1) * 1000.0

    log.info(
        "synthesize done",
        extra={"step": "synth", "run_id": run_id,
               "n_insights": len(insights),
               "insights_ms": round(insights_ms, 2),
               "enrich_ms": round(enrich_ms, 2),
               "assessment_ms": round(assessment_ms, 2),
               "total_synth_ms": round(insights_ms + enrich_ms + assessment_ms, 2)},
    )
    return SynthesisResult(
        insights=insights,
        aggregate_metrics=metrics,
        overall_assessment=overall,
        insights_call_ms=round(insights_ms, 2),
        assessment_call_ms=round(assessment_ms, 2),
    )
