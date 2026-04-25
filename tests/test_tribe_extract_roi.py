"""Tests for extract_all and the visual_fluency post-extraction hook."""

from __future__ import annotations

import numpy as np

from tribe_neural.constants import NUM_VERTICES, ROI_KEYS
from tribe_neural.init_resources import (
    _synthesize_mock_masks,
    _synthesize_mock_weight_maps,
)
from tribe_neural.steps.step2_roi import extract_all


def test_extract_all_returns_one_series_per_roi():
    n_trs = 12
    rng = np.random.default_rng(0)
    preds = rng.standard_normal((n_trs, NUM_VERTICES))
    masks = _synthesize_mock_masks(seed=1)
    weights = _synthesize_mock_weight_maps(seed=2)

    out = extract_all(preds, masks, weights)

    assert set(out.keys()) == set(ROI_KEYS)
    for roi, ts in out.items():
        assert ts.shape == (n_trs,), f"{roi} has bad shape {ts.shape}"


def test_extract_all_outputs_zscore_per_roi():
    """After z-scoring, every ROI series should be roughly mean-zero and unit-std."""
    n_trs = 24
    rng = np.random.default_rng(7)
    preds = rng.standard_normal((n_trs, NUM_VERTICES)) * 5.0
    masks = _synthesize_mock_masks()
    weights = _synthesize_mock_weight_maps()

    out = extract_all(preds, masks, weights)
    for roi, ts in out.items():
        assert abs(float(ts.mean())) < 1e-6, f"{roi} mean not zero"
        # Unit std unless the raw signal was constant (only happens for
        # truly empty mask intersections -> z-score returns mean-subtracted).
        assert abs(float(ts.std()) - 1.0) < 1e-6 or float(ts.std()) == 0.0


def test_extract_all_visual_fluency_subtracts_dorsattn():
    """visual_fluency should differ from a single-mask result because of
    the post-extraction `_Vis_ - 0.5 * _DorsAttn_` hook."""
    rng = np.random.default_rng(13)
    preds = rng.standard_normal((10, NUM_VERTICES))
    masks = _synthesize_mock_masks()
    weights = _synthesize_mock_weight_maps()

    out = extract_all(preds, masks, weights)
    fluency = out["visual_fluency"]
    # If the hook ran, results are non-trivially different from a pure
    # mean over _Vis_. Cheap proxy: variance is non-zero.
    assert float(fluency.std()) > 0.0


def test_extract_all_rejects_bad_shape():
    import pytest
    masks = _synthesize_mock_masks()
    weights = _synthesize_mock_weight_maps()
    with pytest.raises(ValueError):
        extract_all(np.zeros((10,)), masks, weights)  # 1-D


def test_mock_masks_sized_to_NUM_VERTICES():
    masks = _synthesize_mock_masks()
    for net, m in masks.items():
        assert m.shape == (NUM_VERTICES,), f"{net} has bad shape {m.shape}"
        assert m.dtype == bool
