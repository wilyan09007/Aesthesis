"""Tests for `output_builder.build_response` — the pure assembly function
that produces the final `AnalyzeResponse` JSON for the aesthesis-app.

Single-video pivot (DESIGN.md §17): no A/B split. These tests build a
synthetic timeline + events + insights + metrics + assessment, run the
builder, and assert the output shape matches `AnalyzeResponse`. No mocks.
"""

from __future__ import annotations

from aesthesis.output_builder import build_response
from aesthesis.schemas import (
    AggregateMetric,
    Event,
    Insight,
    OverallAssessment,
)


def _timeline(n_trs: int = 3) -> dict:
    return {
        "n_trs": n_trs,
        "tr_duration_s": 1.5,
        "roi_series": {"aesthetic_appeal": [0.0] * n_trs},
        "frames": [
            {"t_s": i * 1.5,
             "composites": {"appeal_index": 0.1 * i, "fluency_score": 0.0,
                            "trust_index": 0.0, "engagement_depth": 0.0,
                            "surprise_polarity": 0.0, "memorability_proxy": 0.0,
                            "ux_dominance": 0.0, "conversion_intent": 0.0}}
            for i in range(n_trs)
        ],
        "windows": [
            {"t_start_s": 0.0, "t_end_s": 4.5, "composites": {"flow_state": True}},
        ],
        "processing_time_ms": 123.4,
    }


def _make_event(t: float = 1.5) -> Event:
    return Event(timestamp_s=t, type="spike",
                 primary_roi="friction_anxiety", magnitude=1.4)


def _make_insight() -> Insight:
    return Insight(
        timestamp_range_s=(1.0, 2.0),
        ux_observation="The CTA is below the fold.",
        recommendation="Move it above the fold.",
        cited_brain_features=["motor_readiness"],
        cited_screen_moment="hero section visible, no CTA",
    )


def _make_metric() -> AggregateMetric:
    return AggregateMetric(
        name="friction_spike_count", value=3.0,
        interpretation="3 friction spike(s) detected",
    )


def _make_assessment() -> OverallAssessment:
    return OverallAssessment(
        summary_paragraph="A short narrative.",
        top_strengths=["good above-fold value at t=2s"],
        top_concerns=["friction spike at t=14s"],
        decisive_moment="t=8s: motor_readiness peaks during pricing reveal",
    )


def test_build_response_shape_matches_analyze_response():
    response = build_response(
        run_id="run-test",
        goal="evaluate the signup flow",
        timeline=_timeline(),
        duration_s=4.5,
        events=[_make_event()],
        insights=[_make_insight()],
        aggregate_metrics=[_make_metric()],
        overall_assessment=_make_assessment(),
        elapsed_ms=1234.0,
    )

    # Top-level fields are flattened (no a/b split).
    assert response.meta.run_id == "run-test"
    assert response.meta.goal == "evaluate the signup flow"
    assert response.duration_s == 4.5
    assert response.elapsed_ms == 1234.0

    # Lists carry through.
    assert len(response.events) == 1
    assert len(response.insights) == 1
    assert len(response.aggregate_metrics) == 1
    assert response.overall_assessment.summary_paragraph == "A short narrative."

    # Timeline summary preserved.
    assert response.timeline.n_trs == 3
    assert response.timeline.tr_duration_s == 1.5
    assert response.timeline.processing_time_ms == 123.4
    assert len(response.timeline.windows) == 1


def test_build_response_no_legacy_ab_fields():
    """Catch regressions: there should be no `a`, `b`, `verdict`, or
    `version` fields anywhere in the new shape."""
    response = build_response(
        run_id="r", goal=None,
        timeline=_timeline(),
        duration_s=4.5,
        events=[_make_event()],
        insights=[_make_insight()],
        aggregate_metrics=[_make_metric()],
        overall_assessment=_make_assessment(),
        elapsed_ms=1.0,
    )

    field_names = set(type(response).model_fields)
    assert "a" not in field_names
    assert "b" not in field_names
    assert "verdict" not in field_names

    # Per-record version tags are also gone (Event, Insight, etc.).
    assert "version" not in type(response.events[0]).model_fields
    assert "version" not in type(response.insights[0]).model_fields


def test_composites_series_extracts_eight_keys():
    """`_summarize_timeline` should pivot per-frame composite dicts into
    per-key lists for each of the 8 composite columns."""
    response = build_response(
        run_id="r", goal=None,
        timeline=_timeline(n_trs=4),
        duration_s=6.0,
        events=[],
        insights=[_make_insight()],
        aggregate_metrics=[_make_metric()],
        overall_assessment=_make_assessment(),
        elapsed_ms=1.0,
    )
    series = response.timeline.composites_series
    expected_keys = {
        "appeal_index", "conversion_intent", "fluency_score", "trust_index",
        "engagement_depth", "surprise_polarity", "memorability_proxy",
        "ux_dominance",
    }
    assert set(series.keys()) == expected_keys
    # Each list has exactly n_trs values.
    for key, values in series.items():
        assert len(values) == 4
    # appeal_index values are 0.0, 0.1, 0.2, 0.3 from the synthetic frames.
    # IEEE 754: compare with tolerance, not equality.
    expected = [0.0, 0.1, 0.2, 0.3]
    assert all(abs(actual - exp) < 1e-9 for actual, exp in zip(series["appeal_index"], expected))
