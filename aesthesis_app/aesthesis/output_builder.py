"""Assemble the final results-page JSON.

The frontend's /results view (DESIGN.md §4.6 view 3) reads exactly this
shape. Keeping the contract pinned in one place makes the frontend's job
easier and gives us a single place to update when the schema evolves.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .schemas import (
    AggregateMetric,
    AnalyzeRequestMeta,
    AnalyzeResponse,
    Event,
    Insight,
    TimelineSummary,
    Verdict,
    VersionResult,
)

log = logging.getLogger(__name__)


def _summarize_timeline(timeline: dict) -> TimelineSummary:
    """Strip the heavy fields from a TRIBE response for the wire.

    The full timeline includes per-TR `values`, `deltas`, `spikes`,
    `co_movement`, `local_peak` and `composites` dicts. The frontend's
    chart only needs:
        - n_trs / tr_duration_s for the X axis
        - roi_series for the 8-curve line chart
        - composites_series for overlay rows (appeal_index etc.)
        - windows for window-level annotations (flow_state highlights)
    The per-TR raw frames stay server-side; we don't need them browser-side.
    """
    composites_keys = {
        "appeal_index", "conversion_intent", "fluency_score", "trust_index",
        "engagement_depth", "surprise_polarity", "memorability_proxy",
        "ux_dominance",
    }
    composites_series: dict[str, list[float]] = {k: [] for k in composites_keys}
    for f in timeline.get("frames", []):
        c = f.get("composites", {})
        for k in composites_keys:
            composites_series[k].append(float(c.get(k, 0.0)))

    return TimelineSummary(
        n_trs=int(timeline.get("n_trs", 0)),
        tr_duration_s=float(timeline.get("tr_duration_s", 1.5)),
        roi_series=timeline.get("roi_series", {}),
        composites_series=composites_series,
        windows=timeline.get("windows", []),
        processing_time_ms=float(timeline.get("processing_time_ms", 0.0)),
    )


def build_response(
    *,
    run_id: str,
    goal: str | None,
    timeline_a: dict,
    timeline_b: dict,
    duration_a: float,
    duration_b: float,
    events_a: list[Event],
    events_b: list[Event],
    insights_a: list[Insight],
    insights_b: list[Insight],
    aggregate_metrics: list[AggregateMetric],
    verdict: Verdict,
    elapsed_ms: float,
    video_url_a: str | None = None,
    video_url_b: str | None = None,
    mock: bool = False,
) -> AnalyzeResponse:
    """Assemble the final JSON. Total per-call work is small — this just
    wraps already-computed pieces into the response model."""
    received_at = datetime.now(timezone.utc).isoformat()

    log.debug(
        "building response",
        extra={"run_id": run_id, "step": "output",
               "n_insights_a": len(insights_a), "n_insights_b": len(insights_b)},
    )

    a = VersionResult(
        version="A",
        video_url=video_url_a,
        duration_s=duration_a,
        timeline=_summarize_timeline(timeline_a),
        events=events_a,
        insights=insights_a,
    )
    b = VersionResult(
        version="B",
        video_url=video_url_b,
        duration_s=duration_b,
        timeline=_summarize_timeline(timeline_b),
        events=events_b,
        insights=insights_b,
    )
    return AnalyzeResponse(
        meta=AnalyzeRequestMeta(goal=goal, run_id=run_id, received_at=received_at),
        a=a, b=b,
        aggregate_metrics=aggregate_metrics,
        verdict=verdict,
        elapsed_ms=round(elapsed_ms, 2),
        mock=mock,
    )
