"""Tests for `_compute_aggregate_metrics` — the pure function that turns a
TRIBE timeline dict into 8 absolute `AggregateMetric` records.

Single-video pivot (DESIGN.md §17): every metric is `(name, value,
interpretation)`, no A/B comparison. These tests exercise the metric
math directly with synthetic timelines — no Gemini, no TRIBE, no mocks.
"""

from __future__ import annotations

from aesthesis.synthesizer import _compute_aggregate_metrics


def _frame(
    *,
    t_s: float = 0.0,
    values: dict[str, float] | None = None,
    spikes: dict[str, bool] | None = None,
    composites: dict[str, float] | None = None,
    dominant: str = "aesthetic_appeal",
) -> dict:
    return {
        "t_s": t_s,
        "values": values or {},
        "spikes": spikes or {},
        "composites": composites or {},
        "dominant": dominant,
    }


def _timeline(frames: list[dict], windows: list[dict] | None = None) -> dict:
    return {
        "frames": frames,
        "windows": windows or [],
        "n_trs": len(frames),
        "tr_duration_s": 1.5,
    }


def test_metrics_emits_all_eight_in_order():
    metrics = _compute_aggregate_metrics(_timeline([]))
    names = [m.name for m in metrics]
    assert names == [
        "mean_appeal_index",
        "mean_cognitive_load",
        "pct_reward_dominance",
        "pct_friction_dominance",
        "friction_spike_count",
        "motor_readiness_peak",
        "flow_state_windows",
        "bounce_risk_windows",
    ]


def test_metrics_have_value_and_interpretation():
    metrics = _compute_aggregate_metrics(_timeline([]))
    for m in metrics:
        assert isinstance(m.value, float)
        assert m.interpretation is not None
        assert len(m.interpretation) > 0


def test_metrics_zero_for_empty_timeline():
    """Empty timeline -> every metric is 0 (not crashing on /0)."""
    metrics = _compute_aggregate_metrics(_timeline([]))
    by_name = {m.name: m.value for m in metrics}
    assert by_name["mean_appeal_index"] == 0.0
    assert by_name["mean_cognitive_load"] == 0.0
    assert by_name["pct_reward_dominance"] == 0.0
    assert by_name["pct_friction_dominance"] == 0.0
    assert by_name["friction_spike_count"] == 0.0
    assert by_name["motor_readiness_peak"] == 0.0
    assert by_name["flow_state_windows"] == 0.0
    assert by_name["bounce_risk_windows"] == 0.0


def test_friction_spike_count_counts_real_spikes():
    frames = [
        _frame(t_s=0.0, spikes={"friction_anxiety": False}),
        _frame(t_s=1.5, spikes={"friction_anxiety": True}),
        _frame(t_s=3.0, spikes={"friction_anxiety": True}),
        _frame(t_s=4.5, spikes={"friction_anxiety": False}),
    ]
    metrics = _compute_aggregate_metrics(_timeline(frames))
    by_name = {m.name: m.value for m in metrics}
    assert by_name["friction_spike_count"] == 2.0


def test_pct_dominance_is_percentage():
    frames = [
        _frame(t_s=0.0, dominant="reward_anticipation"),
        _frame(t_s=1.5, dominant="reward_anticipation"),
        _frame(t_s=3.0, dominant="aesthetic_appeal"),
        _frame(t_s=4.5, dominant="friction_anxiety"),
    ]
    metrics = _compute_aggregate_metrics(_timeline(frames))
    by_name = {m.name: m.value for m in metrics}
    assert by_name["pct_reward_dominance"] == 50.0
    assert by_name["pct_friction_dominance"] == 25.0


def test_motor_readiness_peak_is_max_not_mean():
    frames = [
        _frame(t_s=0.0, values={"motor_readiness": 0.1}),
        _frame(t_s=1.5, values={"motor_readiness": 1.7}),  # peak
        _frame(t_s=3.0, values={"motor_readiness": 0.4}),
    ]
    metrics = _compute_aggregate_metrics(_timeline(frames))
    by_name = {m.name: m.value for m in metrics}
    assert by_name["motor_readiness_peak"] == 1.7


def test_window_counts():
    windows = [
        {"t_start_s": 0.0, "t_end_s": 6.0, "composites": {"flow_state": True}},
        {"t_start_s": 6.0, "t_end_s": 12.0, "composites": {"bounce_risk": True}},
        {"t_start_s": 12.0, "t_end_s": 18.0,
         "composites": {"flow_state": True, "bounce_risk": True}},
    ]
    metrics = _compute_aggregate_metrics(_timeline([], windows=windows))
    by_name = {m.name: m.value for m in metrics}
    assert by_name["flow_state_windows"] == 2.0
    assert by_name["bounce_risk_windows"] == 2.0


def test_appeal_interpretation_phrases():
    """Interpretation copy should reflect direction."""
    pos_frames = [_frame(composites={"appeal_index": 0.5})]
    neg_frames = [_frame(composites={"appeal_index": -0.5})]
    neutral_frames = [_frame(composites={"appeal_index": 0.0})]

    pos_appeal = next(
        m for m in _compute_aggregate_metrics(_timeline(pos_frames))
        if m.name == "mean_appeal_index"
    )
    neg_appeal = next(
        m for m in _compute_aggregate_metrics(_timeline(neg_frames))
        if m.name == "mean_appeal_index"
    )
    neut_appeal = next(
        m for m in _compute_aggregate_metrics(_timeline(neutral_frames))
        if m.name == "mean_appeal_index"
    )
    assert "positive" in pos_appeal.interpretation
    assert "negative" in neg_appeal.interpretation
    assert "neutral" in neut_appeal.interpretation
