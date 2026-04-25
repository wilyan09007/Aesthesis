"""Per-parcel activation reduction (DESIGN.md §5.16, ASSUMPTIONS_BRAIN.md §1.3).

Take the (n_TRs, 20484) per-vertex prediction tensor from TRIBE and reduce
it to (n_TRs, N_PARCELS) — one float per Schaefer-400 parcel per TR. Z-score
each parcel's timeseries across time so the frontend's diverging colormap
(centered at 0) makes visual sense.

This runs in parallel to step2_roi.py; one extracts 8 UX-tuned ROIs
(consumed by the chart and Gemini synthesizer), the other extracts 400
fine-grained parcels (consumed by BrainCortical.tsx for the cortical
visualization). Both feed off the same per-vertex predictions.

Verbose logging at every boundary so a slow / NaN-y / shape-broken run
surfaces immediately.

Output contract:
    parcel_series: ndarray (n_TRs, N_PARCELS) float32, z-scored per parcel.

If a parcel has zero assigned vertices (rare; only happens if the bake's
`vol_to_surf` projection misses it entirely), that parcel emits zeros for
every TR with a logged warning. We do NOT silently propagate NaN.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


#: Total Schaefer parcels. Must match scripts/bake_parcel_map.py.
N_PARCELS: int = 400


def _zscore_per_parcel(arr: np.ndarray) -> np.ndarray:
    """Z-score each column (parcel) across rows (time).

    Mean → 0, std → 1 per parcel. Constant-valued parcels (all-zero row,
    e.g. unassigned) get returned as zeros, not NaN. This mirrors
    step2_roi._zscore but operates over a 2-D shape.

    Args:
        arr: shape (n_TRs, n_parcels), float.

    Returns:
        Same shape as input, dtype float32.
    """
    mean = arr.mean(axis=0, keepdims=True)
    std = arr.std(axis=0, keepdims=True)
    # Mask near-zero stds; fill with 1.0 to make the divide a no-op.
    safe_std = np.where(std < 1e-9, 1.0, std)
    out = (arr - mean) / safe_std
    # Where std was zero, the centered value is 0 - mean — set to 0
    # explicitly to avoid carrying a constant offset into a "z-score".
    out = np.where(std < 1e-9, 0.0, out)
    return out.astype(np.float32, copy=False)


def extract_parcels(
    preds: np.ndarray,
    parcel_map: np.ndarray,
) -> np.ndarray:
    """Reduce per-vertex predictions to a per-parcel z-scored series.

    Args:
        preds: shape (n_TRs, n_vertices). Output of TRIBE.
        parcel_map: shape (n_vertices,) of uint16. Each entry is a parcel
            index in [0, N_PARCELS]. 0 = unassigned (skipped).

    Returns:
        ndarray of shape (n_TRs, N_PARCELS), dtype float32.
        Each parcel's column is z-scored across time. Unassigned parcels
        (no vertices) emit zeros and emit a logged warning the first
        time they're seen for this run.

    Raises:
        ValueError: if shapes mismatch or inputs are degenerate. This is a
            hard fail — we never want to silently propagate a broken
            prediction tensor downstream into the frontend.
    """
    if preds.ndim != 2:
        raise ValueError(
            f"preds must be 2-D (n_TRs, n_vertices); got {preds.shape}"
        )
    n_trs, n_vertices = preds.shape

    if parcel_map.ndim != 1:
        raise ValueError(
            f"parcel_map must be 1-D (n_vertices,); got {parcel_map.shape}"
        )
    if parcel_map.shape[0] != n_vertices:
        raise ValueError(
            f"parcel_map length {parcel_map.shape[0]} != preds vertex count "
            f"{n_vertices}. Likely an atlas/mesh version mismatch."
        )

    # Loud failure on NaN/Inf — the visualization can't render and Gemini
    # would receive garbage. Better to crash than to ship.
    if not np.isfinite(preds).all():
        n_bad = int((~np.isfinite(preds)).sum())
        raise ValueError(
            f"preds contains {n_bad} non-finite values (NaN/Inf). "
            "Upstream TRIBE output is broken; refusing to reduce."
        )

    log.info(
        "extract_parcels begin",
        extra={
            "step": "parcels",
            "shape": list(preds.shape),
            "n_trs": int(n_trs),
            "n_vertices": int(n_vertices),
            "n_parcels": N_PARCELS,
            "preds_dtype": str(preds.dtype),
            "parcel_map_dtype": str(parcel_map.dtype),
        },
    )

    # ── Group vertices by parcel index ──────────────────────────────────────
    # Build a list of vertex indices per parcel. We could do this in pure
    # numpy with bincount + argsort, but a Python loop here is fine (one-shot,
    # 20k iters). If it ever shows up on the profile, swap for an offset
    # array + np.add.reduceat.
    out = np.zeros((n_trs, N_PARCELS), dtype=np.float64)
    empty_parcels: list[int] = []

    for parcel_idx in range(1, N_PARCELS + 1):  # 1-indexed; 0 is unassigned
        mask = parcel_map == parcel_idx
        n_in_parcel = int(mask.sum())
        if n_in_parcel == 0:
            empty_parcels.append(parcel_idx)
            # Column stays at zeros; z-score later will keep it at zeros.
            continue
        # Mean activation across all vertices in this parcel, per TR.
        # preds[:, mask] is (n_trs, n_in_parcel); .mean(axis=1) → (n_trs,).
        # Result lands in out[:, parcel_idx - 1] so the column index is
        # 0-based for downstream consumers (matches typical np conventions).
        out[:, parcel_idx - 1] = preds[:, mask].mean(axis=1)

    if empty_parcels:
        log.warning(
            "parcels with zero assigned vertices: %d (will be zeroed in output). "
            "Sample IDs: %s",
            len(empty_parcels),
            empty_parcels[:10],
        )

    # ── Z-score per parcel ──────────────────────────────────────────────────
    out_z = _zscore_per_parcel(out)

    # Sanity stats for debugging downstream rendering issues.
    nonzero_parcels = int(np.count_nonzero(out.sum(axis=0)))
    overall_min = float(out_z.min())
    overall_max = float(out_z.max())
    overall_mean_abs = float(np.abs(out_z).mean())

    log.info(
        "extract_parcels done",
        extra={
            "step": "parcels",
            "n_parcels_with_data": nonzero_parcels,
            "n_parcels_empty": len(empty_parcels),
            "z_min": round(overall_min, 4),
            "z_max": round(overall_max, 4),
            "z_mean_abs": round(overall_mean_abs, 4),
            "out_shape": list(out_z.shape),
            "out_dtype": str(out_z.dtype),
        },
    )

    if nonzero_parcels < N_PARCELS * 0.85:
        # Loud warning, not a raise. The pipeline should still complete
        # (the chart/Gemini path doesn't depend on parcels), but the
        # visualization is going to look bad. Surface it.
        log.warning(
            "only %d / %d parcels have non-zero activations across the whole "
            "clip — the cortical brain visualization will look mostly empty. "
            "Check the parcel map (data/schaefer400_parcels.npy).",
            nonzero_parcels, N_PARCELS,
        )

    return out_z
