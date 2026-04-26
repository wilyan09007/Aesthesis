"""Tests for the per-parcel reduction step.

No mocks. Every test runs against real numpy arrays — the user's hard
rule is that mocks introduce divergence between test reality and
production behavior. ASSUMPTIONS_BRAIN.md §6.

Each test fails loudly with a named cause when an assumption breaks. We
do NOT use `pytest.skip` for missing artifacts — the artifact-dependent
test (`test_loaded_parcel_map_is_well_formed`) raises a clear
AssertionError naming the bake command needed to fix it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from tribe_neural.constants import NUM_VERTICES
from tribe_neural.steps.step2b_parcels import (
    N_PARCELS,
    _zscore_per_parcel,
    extract_parcels,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_parcel_map(n_per_parcel: int = 50) -> np.ndarray:
    """Construct a synthetic parcel map: parcel `i+1` covers a contiguous
    block of `n_per_parcel` vertices. Vertices past the last block stay 0.

    This is NOT a mock of TRIBE — it's a deterministic test fixture
    built from numpy primitives. Allowed under the no-mocks rule
    (ASSUMPTIONS_BRAIN.md §6).
    """
    arr = np.zeros(NUM_VERTICES, dtype=np.uint16)
    for parcel_idx in range(1, N_PARCELS + 1):
        start = (parcel_idx - 1) * n_per_parcel
        end = start + n_per_parcel
        if end > NUM_VERTICES:
            break
        arr[start:end] = parcel_idx
    return arr


def _make_preds(n_trs: int = 12, seed: int = 7) -> np.ndarray:
    """Synthetic predictions of shape (n_trs, NUM_VERTICES). Each vertex
    gets a random walk so the resulting per-parcel z-scores are non-trivial.
    """
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n_trs, NUM_VERTICES)).astype(np.float32)


# ─── Shape contract ──────────────────────────────────────────────────────────

def test_extract_parcels_shape_contract() -> None:
    """Output must be (n_TRs, N_PARCELS) regardless of input n_TRs."""
    parcels = _make_parcel_map()
    for n_trs in (1, 7, 22):
        preds = _make_preds(n_trs=n_trs)
        out = extract_parcels(preds, parcels)
        assert out.shape == (n_trs, N_PARCELS), (
            f"expected ({n_trs}, {N_PARCELS}), got {out.shape}"
        )
        assert out.dtype == np.float32, f"expected float32, got {out.dtype}"


# ─── Z-score property ────────────────────────────────────────────────────────

def test_zscored_per_parcel_columns_are_normalized() -> None:
    """Each populated parcel column should have mean ≈ 0, std ≈ 1."""
    parcels = _make_parcel_map()
    preds = _make_preds(n_trs=20)
    out = extract_parcels(preds, parcels)

    populated_cols = np.where(out.std(axis=0) > 0)[0]
    assert populated_cols.size > 0, "no parcels with non-zero variance — test setup broke"

    means = out[:, populated_cols].mean(axis=0)
    stds = out[:, populated_cols].std(axis=0)
    assert np.allclose(means, 0.0, atol=1e-5), (
        f"populated parcel means deviate from 0: max={np.abs(means).max():.4e}"
    )
    assert np.allclose(stds, 1.0, atol=1e-5), (
        f"populated parcel stds deviate from 1: max_dev={(stds - 1).max():.4e}"
    )


def test_zscore_helper_handles_constant_columns() -> None:
    """A column with zero variance should come out as zeros, not NaN."""
    arr = np.zeros((5, 3), dtype=np.float64)
    arr[:, 0] = 7.0  # constant
    arr[:, 1] = np.arange(5)
    # column 2 stays at zeros
    out = _zscore_per_parcel(arr)

    assert out.shape == arr.shape
    assert np.all(np.isfinite(out)), "z-score must never produce NaN/Inf"
    assert np.allclose(out[:, 0], 0.0), "constant column must z-score to zeros"
    assert np.allclose(out[:, 2], 0.0), "all-zero column must stay zeros"
    # column 1 should be properly z-scored
    assert np.isclose(out[:, 1].mean(), 0.0, atol=1e-7)
    assert np.isclose(out[:, 1].std(), 1.0, atol=1e-7)


# ─── Empty parcels ──────────────────────────────────────────────────────────

def test_unassigned_parcels_emit_zeros_not_nan(caplog: pytest.LogCaptureFixture) -> None:
    """If a parcel has zero assigned vertices, the output column for that
    parcel must be all zeros AND a warning must be logged. Silent NaN
    propagation is the failure mode we're guarding against."""
    # Construct a map that intentionally skips parcel 42.
    parcels = _make_parcel_map(n_per_parcel=20)
    parcels[parcels == 42] = 0  # remove parcel 42 entirely

    preds = _make_preds(n_trs=8)

    with caplog.at_level(logging.WARNING, logger="tribe_neural.steps.step2b_parcels"):
        out = extract_parcels(preds, parcels)

    assert np.all(np.isfinite(out)), "extract_parcels must never emit NaN/Inf"
    assert np.allclose(out[:, 41], 0.0), (
        "parcel 42 (index 41) should be all zeros after removal"
    )
    warned = any("zero assigned vertices" in rec.message for rec in caplog.records)
    assert warned, "expected a warning log for the empty parcel"


# ─── Loud failures ──────────────────────────────────────────────────────────

def test_nonfinite_input_raises_loudly() -> None:
    """NaN or Inf in `preds` is a hard failure — never silently propagate."""
    parcels = _make_parcel_map()
    preds = _make_preds(n_trs=4)
    preds[2, 100] = np.nan

    with pytest.raises(ValueError, match="non-finite"):
        extract_parcels(preds, parcels)


def test_shape_mismatch_raises() -> None:
    """parcel_map length must match preds vertex count."""
    parcels = np.zeros(NUM_VERTICES + 5, dtype=np.uint16)
    preds = _make_preds(n_trs=3)
    with pytest.raises(ValueError, match="parcel_map length"):
        extract_parcels(preds, parcels)


def test_wrong_preds_dimension_raises() -> None:
    """preds must be 2-D."""
    preds_3d = np.zeros((2, 3, NUM_VERTICES), dtype=np.float32)
    parcels = _make_parcel_map()
    with pytest.raises(ValueError, match="must be 2-D"):
        extract_parcels(preds_3d, parcels)


# ─── Real artifact (parcel map on disk) ─────────────────────────────────────

def test_loaded_parcel_map_is_well_formed() -> None:
    """If the real Schaefer-400 parcel map has been baked, it should
    satisfy the same contract the runtime expects.

    Failure is loud and actionable: tells the caller exactly what
    command to run. We do NOT pytest.skip — the user's instruction is
    that missing real-world artifacts should fail loudly so the gap is
    obvious.
    """
    import os

    data_dir = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    path = data_dir / "schaefer400_parcels.npy"
    if not path.exists():
        # Fail loudly with the runbook command. ASSUMPTIONS_BRAIN.md §6.
        pytest.fail(
            f"baked artifact missing: {path}\n"
            "Run `python -m tribe_service.scripts.bake_parcel_map` to generate it. "
            "(This test intentionally does NOT skip — the user's no-mocks rule "
            "requires loud failures for real-world artifact gaps.)",
            pytrace=False,
        )

    arr = np.load(path)
    assert arr.shape == (NUM_VERTICES,), (
        f"baked parcel map shape {arr.shape}; expected ({NUM_VERTICES},). "
        f"Re-run the bake script."
    )
    assert arr.dtype in (np.uint16, np.int16, np.int32, np.int64, np.uint8), (
        f"unexpected parcel map dtype {arr.dtype}"
    )
    assert int(arr.max()) <= N_PARCELS, (
        f"baked parcel map contains parcel index {int(arr.max())} > {N_PARCELS}"
    )
    assert int(arr.min()) >= 0, (
        f"baked parcel map contains negative parcel index {int(arr.min())}"
    )
    coverage = float(np.count_nonzero(arr)) / arr.size
    assert coverage > 0.85, (
        f"baked parcel map coverage {coverage:.1%} below 85%. "
        f"Atlas projection looks broken — re-bake or investigate."
    )
