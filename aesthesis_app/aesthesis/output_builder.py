"""Assemble the final results-page JSON.

The frontend's ``/results`` view (DESIGN.md §4.6 view 3, post-pivot §17)
reads exactly this shape. Keeping the contract pinned in one place makes
the frontend's job easier and gives us a single place to update when the
schema evolves.

Pre-pivot this returned a dual-subject ``AnalyzeResponse`` (``a`` +
``b`` + verdict). The pivot collapsed it to single-subject — see DESIGN.md
§17.
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
    OverallAssessment,
    TimelineSummary,
)

log = logging.getLogger(__name__)


def _summarize_timeline(timeline: dict) -> TimelineSummary:
    """Strip the heavy fields from a TRIBE response for the wire.

    The full timeline includes per-TR ``values``, ``deltas``, ``spikes``,
    ``co_movement``, ``local_peak`` and ``composites`` dicts. The frontend's
    chart only needs:
        - ``n_trs`` / ``tr_duration_s`` for the X axis
        - ``roi_series`` for the 8-curve line chart
        - ``composites_series`` for overlay rows (appeal_index etc.)
        - ``windows`` for window-level annotations (flow_state highlights)
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

    # parcel_series is optional. Only the TRIBE worker that has
    # data/schaefer400_parcels.npy on disk attaches it. Old workers don't
    # send the field; new workers send (n_TRs, 400) of float z-scores.
    # ASSUMPTIONS_BRAIN.md §1.3 + §4.1.
    parcel_series = timeline.get("parcel_series")
    if parcel_series is not None:
        log.debug(
            "timeline carries parcel_series",
            extra={"step": "output", "n_trs": len(parcel_series),
                   "n_parcels": len(parcel_series[0]) if parcel_series else 0},
        )

    face_colors = timeline.get("face_colors")
    if face_colors is not None:
        log.debug(
            "timeline carries face_colors",
            extra={"step": "output",
                   "lh_kb": round(len(face_colors["left"]["data_b64"]) / 1024, 1),
                   "rh_kb": round(len(face_colors["right"]["data_b64"]) / 1024, 1)},
        )

    return TimelineSummary(
        n_trs=int(timeline.get("n_trs", 0)),
        tr_duration_s=float(timeline.get("tr_duration_s", 1.5)),
        roi_series=timeline.get("roi_series", {}),
        composites_series=composites_series,
        windows=timeline.get("windows", []),
        processing_time_ms=float(timeline.get("processing_time_ms", 0.0)),
        parcel_series=parcel_series,
        face_colors=face_colors,
    )


def build_response(
    *,
    run_id: str,
    goal: str | None,
    timeline: dict,
    duration_s: float,
    events: list[Event],
    insights: list[Insight],
    aggregate_metrics: list[AggregateMetric],
    overall_assessment: OverallAssessment,
    elapsed_ms: float,
    video_url: str | None = None,
) -> AnalyzeResponse:
    """Assemble the final JSON. Total per-call work is small — this just
    wraps already-computed pieces into the response model."""
    received_at = datetime.now(timezone.utc).isoformat()

    log.debug(
        "building response",
        extra={"run_id": run_id, "step": "output",
               "n_events": len(events), "n_insights": len(insights),
               "n_metrics": len(aggregate_metrics)},
    )

    return AnalyzeResponse(
        meta=AnalyzeRequestMeta(goal=goal, run_id=run_id, received_at=received_at),
        video_url=video_url,
        duration_s=duration_s,
        timeline=_summarize_timeline(timeline),
        events=events,
        insights=insights,
        aggregate_metrics=aggregate_metrics,
        overall_assessment=overall_assessment,
        elapsed_ms=round(elapsed_ms, 2),
    )
