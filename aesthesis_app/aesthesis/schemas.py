"""Pydantic models for the public API and internal contracts.

Two surfaces:
- `AnalyzeResponse` is the JSON the frontend results page consumes.
- `Insight`, `Verdict`, `Event` etc. are the internal Gemini-output / event
  shapes — they appear inside `AnalyzeResponse` too, but the rest of the
  pipeline uses them directly.

Schemas track DESIGN.md §4.5 input/output contracts verbatim.
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

VersionTag = Literal["A", "B"]


class Event(BaseModel):
    """Deterministic event extracted from a brain timeline."""
    version: VersionTag
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
    version: VersionTag
    timestamp_range_s: tuple[float, float]
    ux_observation: str
    recommendation: str
    cited_brain_features: list[str]
    cited_screen_moment: str


class AggregateMetric(BaseModel):
    name: str
    a: float
    b: float
    edge: VersionTag | Literal["tie"]
    edge_description: str | None = None


class Verdict(BaseModel):
    """Output of the head-to-head verdict call (DESIGN.md §4.5 step 3)."""
    winner: VersionTag | Literal["tie"]
    summary_paragraph: str
    version_a_strengths: list[str]
    version_b_strengths: list[str]
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


class VersionResult(BaseModel):
    version: VersionTag
    video_url: str | None = None
    duration_s: float
    timeline: TimelineSummary
    events: list[Event]
    insights: list[Insight]


class AnalyzeRequestMeta(BaseModel):
    goal: str | None = None
    run_id: str
    received_at: str  # ISO 8601


class AnalyzeResponse(BaseModel):
    """Top-level result returned by `POST /api/analyze`."""
    meta: AnalyzeRequestMeta
    a: VersionResult
    b: VersionResult
    aggregate_metrics: list[AggregateMetric]
    verdict: Verdict
    elapsed_ms: float


class ValidationFailure(BaseModel):
    """400 response body shape."""
    field: str
    error: str
    details: dict | None = None
