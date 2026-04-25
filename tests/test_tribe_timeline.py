"""Tests for build_timeline + the per-TR + window structure."""

from __future__ import annotations

import numpy as np
import pytest

from tribe_neural.constants import ROI_KEYS, TR_DURATION
from tribe_neural.steps.step7_timeline import build_timeline


def _fake_roi_ts(n_trs: int, seed: int = 11) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {k: rng.standard_normal(n_trs) for k in ROI_KEYS}


def test_build_timeline_rejects_empty():
    with pytest.raises(ValueError):
        build_timeline({}, window_trs=4, step_trs=1)


def test_build_timeline_basic_structure():
    n_trs = 20
    payload = build_timeline(_fake_roi_ts(n_trs), window_trs=4, step_trs=1)

    assert payload["n_trs"] == n_trs
    assert payload["tr_duration_s"] == TR_DURATION
    assert len(payload["frames"]) == n_trs
    assert len(payload["windows"]) == n_trs - 4 + 1
    assert payload["window_config"] == {"window_trs": 4, "step_trs": 1}
    assert set(payload["roi_series"].keys()) == set(ROI_KEYS)


def test_frame_carries_required_fields():
    n_trs = 8
    payload = build_timeline(_fake_roi_ts(n_trs), window_trs=4, step_trs=1)

    frame = payload["frames"][3]
    for field in ("t_s", "values", "deltas", "dominant", "dominant_shift",
                  "local_peak", "spikes", "co_movement", "composites"):
        assert field in frame
    assert frame["t_s"] == 3 * TR_DURATION
    assert set(frame["values"].keys()) == set(ROI_KEYS)
    assert set(frame["composites"].keys()) >= {
        "appeal_index", "conversion_intent", "fluency_score", "trust_index",
        "engagement_depth", "surprise_polarity", "memorability_proxy",
        "ux_dominance",
    }


def test_window_carries_stats_connectivity_composites():
    n_trs = 12
    payload = build_timeline(_fake_roi_ts(n_trs), window_trs=4, step_trs=1)
    w = payload["windows"][0]
    assert "t_start_s" in w and "t_end_s" in w
    assert "stats" in w
    assert "connectivity" in w
    assert "composites" in w
    # Stats should have one entry per ROI.
    assert set(w["stats"].keys()) == set(ROI_KEYS)
    # Composites — bool / float gates.
    for k in ("flow_state", "decision_clarity", "bounce_risk", "hook_strength",
              "aesthetic_dwell", "friction_burst"):
        assert k in w["composites"]


def test_windows_below_minimum_length():
    """If we don't have enough TRs, windows should be empty rather than crash."""
    payload = build_timeline(_fake_roi_ts(3), window_trs=4, step_trs=1)
    assert payload["windows"] == []
    # frames still emit
    assert len(payload["frames"]) == 3


def test_dominant_shift_flag_round_trip():
    """Construct a clear dominant-shift signal, check the flag fires."""
    keys = list(ROI_KEYS)
    n_trs = 6
    matrix = np.zeros((n_trs, len(keys)))
    # First 3 TRs: aesthetic_appeal dominant
    matrix[:3, keys.index("aesthetic_appeal")] = 1.0
    # Next 3: friction_anxiety dominant
    matrix[3:, keys.index("friction_anxiety")] = 1.0
    roi_ts = {k: matrix[:, i] for i, k in enumerate(keys)}
    payload = build_timeline(roi_ts, window_trs=4, step_trs=1)
    flags = [f["dominant_shift"] for f in payload["frames"]]
    assert flags[3] is True  # the transition TR
    assert flags[0] is False
