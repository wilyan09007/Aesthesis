"""Event-extraction tests against synthetic timelines.

Pure-function tests on `extract_events`. No mocks. The function takes a
dict (TRIBE timeline shape) and returns a list of Event records.
"""

from __future__ import annotations

from aesthesis.events import extract_events, EVENT_CAP


def _make_timeline(frames: list[dict], windows: list[dict] | None = None) -> dict:
    return {
        "frames": frames,
        "windows": windows or [],
        "n_trs": len(frames),
        "tr_duration_s": 1.5,
    }


def _frame(
    t_s: float,
    *,
    values: dict[str, float] | None = None,
    deltas: dict[str, float] | None = None,
    dominant: str = "aesthetic_appeal",
    dominant_shift: bool = False,
    spikes: dict[str, bool] | None = None,
    composites: dict[str, float] | None = None,
) -> dict:
    return {
        "t_s": t_s,
        "values": values or {},
        "deltas": deltas or {},
        "dominant": dominant,
        "dominant_shift": dominant_shift,
        "local_peak": {},
        "spikes": spikes or {},
        "co_movement": {},
        "composites": composites or {},
    }


def test_extract_events_empty_timeline():
    assert extract_events(_make_timeline([])) == []


def test_extract_events_picks_up_spike():
    frames = [
        _frame(0.0),
        _frame(1.5,
               spikes={"friction_anxiety": True},
               deltas={"friction_anxiety": 1.4}),
        _frame(3.0),
    ]
    out = extract_events(_make_timeline(frames))
    spike_events = [e for e in out if e.type == "spike"]
    assert len(spike_events) == 1
    assert spike_events[0].primary_roi == "friction_anxiety"
    assert spike_events[0].magnitude > 1.0


def test_extract_events_picks_up_dominant_shift():
    frames = [
        _frame(0.0, dominant="aesthetic_appeal", values={"aesthetic_appeal": 1.0}),
        _frame(1.5, dominant="friction_anxiety", dominant_shift=True,
               values={"friction_anxiety": 1.0}),
    ]
    out = extract_events(_make_timeline(frames))
    shifts = [e for e in out if e.type == "dominant_shift"]
    assert len(shifts) == 1
    assert shifts[0].primary_roi == "friction_anxiety"


def test_extract_events_picks_up_sustained():
    """3+ TRs of the same dominant ROI -> one sustained event."""
    frames = [
        _frame(0.0, dominant="aesthetic_appeal", values={"aesthetic_appeal": 1.0}),
        _frame(1.5, dominant="aesthetic_appeal", values={"aesthetic_appeal": 1.1}),
        _frame(3.0, dominant="aesthetic_appeal", values={"aesthetic_appeal": 1.2}),
        _frame(4.5, dominant="aesthetic_appeal", values={"aesthetic_appeal": 0.9}),
    ]
    out = extract_events(_make_timeline(frames))
    sustained = [e for e in out if e.type == "sustained"]
    assert len(sustained) == 1


def test_extract_events_picks_up_trough():
    """Trough = appeal_index < -0.3 AND friction > 0.7."""
    frames = [
        _frame(0.0, values={"friction_anxiety": 0.9},
               composites={"appeal_index": -0.5}),
    ]
    out = extract_events(_make_timeline(frames))
    troughs = [e for e in out if e.type == "trough"]
    assert len(troughs) == 1


def test_extract_events_window_signals():
    windows = [
        {"t_start_s": 0.0, "t_end_s": 6.0, "composites": {"flow_state": True}},
        {"t_start_s": 6.0, "t_end_s": 12.0, "composites": {"bounce_risk": True}},
    ]
    out = extract_events(_make_timeline([_frame(0.0)], windows=windows))
    flows = [e for e in out if e.type == "flow"]
    bounces = [e for e in out if e.type == "bounce_risk"]
    assert len(flows) == 1
    assert len(bounces) == 1


def test_extract_events_caps_at_event_cap():
    """Lots of spikes -> truncated to EVENT_CAP, sorted by timestamp."""
    frames = []
    for i in range(40):
        frames.append(_frame(
            t_s=i * 1.5,
            spikes={"friction_anxiety": True},
            deltas={"friction_anxiety": float(i + 1)},
        ))
    out = extract_events(_make_timeline(frames))
    assert len(out) <= EVENT_CAP
    # Output should be in ascending time order.
    assert all(out[i].timestamp_s <= out[i + 1].timestamp_s for i in range(len(out) - 1))
