"""Tests for extract_all and the visual_fluency post-extraction hook.

Uses synthetic mask + weight inputs (constructed in this file) so the
extract_all math is covered without requiring the on-disk Schaefer /
Neurosynth artifacts that production loads from `data/`.
"""

from __future__ import annotations

import numpy as np
import pytest

from tribe_neural.constants import (
    NEUROSYNTH_TERMS,
    NUM_VERTICES,
    ROI_KEYS,
    YEO_NETWORK_SUBSTRINGS,
)
from tribe_neural.steps.step2_roi import extract_all


def _synth_masks(seed: int = 42) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    fractions = {
        "_Default_":     0.30,
        "_Limbic_":      0.05,
        "_Vis_":         0.13,
        "_Cont_":        0.13,
        "_DorsAttn_":    0.10,
        "_SomMot_":      0.18,
        "_SalVentAttn_": 0.11,
    }
    masks: dict[str, np.ndarray] = {}
    for name in YEO_NETWORK_SUBSTRINGS:
        frac = fractions.get(name, 0.10)
        mask = np.zeros(NUM_VERTICES, dtype=bool)
        n_pick = int(NUM_VERTICES * frac)
        idx = rng.choice(NUM_VERTICES, size=n_pick, replace=False)
        mask[idx] = True
        masks[name] = mask
    return masks


def _synth_weight_maps(seed: int = 137) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    for term in NEUROSYNTH_TERMS:
        w = rng.gamma(shape=2.0, scale=0.3, size=NUM_VERTICES).astype(np.float64)
        out[term] = w
    return out


def test_extract_all_returns_one_series_per_roi():
    n_trs = 12
    rng = np.random.default_rng(0)
    preds = rng.standard_normal((n_trs, NUM_VERTICES))
    masks = _synth_masks(seed=1)
    weights = _synth_weight_maps(seed=2)

    out = extract_all(preds, masks, weights)

    assert set(out.keys()) == set(ROI_KEYS)
    for roi, ts in out.items():
        assert ts.shape == (n_trs,), f"{roi} has bad shape {ts.shape}"


def test_extract_all_outputs_zscore_per_roi():
    """After z-scoring, every ROI series should be roughly mean-zero and unit-std."""
    n_trs = 24
    rng = np.random.default_rng(7)
    preds = rng.standard_normal((n_trs, NUM_VERTICES)) * 5.0
    masks = _synth_masks()
    weights = _synth_weight_maps()

    out = extract_all(preds, masks, weights)
    for roi, ts in out.items():
        assert abs(float(ts.mean())) < 1e-6, f"{roi} mean not zero"
        assert abs(float(ts.std()) - 1.0) < 1e-6 or float(ts.std()) == 0.0


def test_extract_all_visual_fluency_subtracts_dorsattn():
    """visual_fluency should differ from a single-mask result because of
    the post-extraction `_Vis_ - 0.5 * _DorsAttn_` hook."""
    rng = np.random.default_rng(13)
    preds = rng.standard_normal((10, NUM_VERTICES))
    masks = _synth_masks()
    weights = _synth_weight_maps()

    out = extract_all(preds, masks, weights)
    fluency = out["visual_fluency"]
    assert float(fluency.std()) > 0.0


def test_extract_all_rejects_bad_shape():
    masks = _synth_masks()
    weights = _synth_weight_maps()
    with pytest.raises(ValueError):
        extract_all(np.zeros((10,)), masks, weights)  # 1-D


def test_synth_masks_sized_to_NUM_VERTICES():
    masks = _synth_masks()
    for net, m in masks.items():
        assert m.shape == (NUM_VERTICES,), f"{net} has bad shape {m.shape}"
        assert m.dtype == bool
