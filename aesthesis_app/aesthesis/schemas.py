"""Pydantic models for the public API and internal contracts.

Single-video pipeline (post-pivot, DESIGN.md §17):

    POST /api/analyze      multipart {video, [goal]}
        -> AnalyzeResponse {
              meta, video_url, duration_s,
              timeline, events, insights,
              aggregate_metrics, overall_assessment,
              elapsed_ms,
           }

The frontend's ``/results`` view consumes this exactly. The backend
internal pipeline uses ``Event``, ``Insight``, ``AggregateMetric``,
``OverallAssessment`` directly between modules.

Pre-pivot history (A/B comparison) used a ``VersionTag = "A" | "B"``
discriminator on every record + a ``Verdict`` block declaring a winner.
That whole concept is gone — see DESIGN.md §17 for the rationale.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Discriminator type for events (DESIGN.md §4.5 step 1).
EventType = Literal[
    "spike",
    "dominant_shift",
    "sustained",
    "co_movement",
    "trough",
    "flow",
    "bounce_risk",
]


class Event(BaseModel):
    """Deterministic event extracted from a brain timeline."""
    timestamp_s: float
    type: EventType
    primary_roi: str | None = None
    magnitude: float = 0.0
    co_events: list[str] = Field(default_factory=list)
    agent_action_at_t: str | None = None
    screenshot_path: str | None = None  # local path on the app server
    screenshot_b64: str | None = None    # included in Gemini payload only


class Insight(BaseModel):
    """One Gemini insight per event (DESIGN.md §4.5 output schema)."""
    timestamp_range_s: tuple[float, float]
    ux_observation: str
    recommendation: str
    cited_brain_features: list[str]
    cited_screen_moment: str


class AggregateMetric(BaseModel):
    """Single absolute metric scored against this video.

    Pre-pivot this carried a/b/edge for A/B comparison. Post-pivot every
    metric is a self-contained `(name, value, interpretation)`. The
    ``interpretation`` string is short human-facing context — the metric
    is otherwise opaque to the frontend.
    """
    name: str
    value: float
    interpretation: str | None = None


class OverallAssessment(BaseModel):
    """Output of the second Gemini call — narrative summary of the demo.

    Replaces the pre-pivot ``Verdict`` (which picked a winner between A and
    B). The new shape narrates a single demo: holistic summary, what the
    brain said worked, what it flagged, and the most memorable timestamp.
    """
    summary_paragraph: str
    top_strengths: list[str]
    top_concerns: list[str]
    decisive_moment: str


class TimelineSummary(BaseModel):
    """A pruned version of the TRIBE response for the results UI.

    Carries everything the frontend's ROI line chart needs without bloating
    the payload with full per-vertex predictions (those are 20484 floats
    per TR — too big to ship to the browser).
    """
    n_trs: int
    tr_duration_s: float
    roi_series: dict[str, list[float]]
    composites_series: dict[str, list[float]] = Field(default_factory=dict)
    windows: list[dict] = Field(default_factory=list)
    processing_time_ms: float = 0.0
    #: Per-parcel z-scored activations on the Schaefer-400 atlas, projected
    #: to fsaverage5. Shape (n_TRs, 400). Drives the cortical brain in
    #: BrainCortical.tsx. Optional: missing when the bake script hasn't
    #: been run on the TRIBE worker; the frontend then falls back to the
    #: placeholder geometry. See ASSUMPTIONS_BRAIN.md §1.3 / §3.6.
    #:
    #: Wire size: ~32 KB for a 30s clip (20 TRs × 400 floats × 4 bytes).
    parcel_series: list[list[float]] | None = None


class AnalyzeRequestMeta(BaseModel):
    goal: str | None = None
    run_id: str
    received_at: str  # ISO 8601


class AnalyzeResponse(BaseModel):
    """Top-level result returned by ``POST /api/analyze`` (single-video)."""
    meta: AnalyzeRequestMeta
    video_url: str | None = None
    duration_s: float
    timeline: TimelineSummary
    events: list[Event]
    insights: list[Insight]
    aggregate_metrics: list[AggregateMetric]
    overall_assessment: OverallAssessment
    elapsed_ms: float


class ValidationFailure(BaseModel):
    """400 response body shape."""
    field: str
    error: str
    details: dict | None = None
