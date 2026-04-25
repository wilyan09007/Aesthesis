"""Build the per-TR + sliding-window response from a per-ROI timeseries dict.

Implements DESIGN.md §5.3 Path B (B1 + B2) for the UX keyset, then formats
the result as the JSON payload exposed by `/process_video_timeline`.
"""

from __future__ import annotations

import logging
from typing import Mapping

import numpy as np

from ..constants import (
    PAIRS_UX,
    ROI_KEYS,
    SPIKE_K,
    STEP_TRS_DEFAULT,
    TR_DURATION,
    WINDOW_TRS_DEFAULT,
)
from .step3_stats import extract_stats
from .step4_connectivity import compute_connectivity
from .step5_composites import (
    compute_per_tr_composites,
    compute_window_composites,
    PER_TR_COMPOSITES,
)

log = logging.getLogger(__name__)


def _delta_sigmas(roi_ts: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Per-ROI sigma of single-TR deltas. Used as the spike-detection
    noise floor."""
    out: dict[str, float] = {}
    for roi, ts in roi_ts.items():
        if ts.size < 2:
            out[roi] = 1.0
            continue
        deltas = np.diff(ts)
        s = float(deltas.std())
        out[roi] = max(s, 1e-6)  # never zero — keep spike check well-defined
    return out


def _build_per_tr_frames(roi_ts: Mapping[str, np.ndarray]) -> list[dict]:
    """B1 — one frame per TR.

    Each frame has the structure described in DESIGN.md §5.3 B1 plus the
    per-TR composite values from §5.16.3.
    """
    keys = list(roi_ts.keys())
    if not keys:
        return []
    # Stack into (n_TRs, n_keys) for vectorized work.
    matrix = np.stack([roi_ts[k] for k in keys], axis=1)
    n_trs = matrix.shape[0]
    deltas = np.zeros_like(matrix)
    if n_trs > 1:
        deltas[1:] = np.diff(matrix, axis=0)

    sigma = _delta_sigmas(roi_ts)
    frames: list[dict] = []

    # Precompute dominant ROI per TR for the dominant_shift flag.
    dominant_idx = matrix.argmax(axis=1)

    for t in range(n_trs):
        values = {keys[i]: float(matrix[t, i]) for i in range(len(keys))}
        delta_vals = {keys[i]: float(deltas[t, i]) for i in range(len(keys))}

        # local peak: value at t is strictly greater than both neighbours
        local_peak: dict[str, bool] = {}
        for i, k in enumerate(keys):
            prev = matrix[t - 1, i] if t > 0 else -np.inf
            nxt = matrix[t + 1, i] if t < n_trs - 1 else -np.inf
            local_peak[k] = bool(matrix[t, i] > prev and matrix[t, i] > nxt)

        spikes: dict[str, bool] = {
            k: bool(delta_vals[k] > SPIKE_K * sigma[k]) for k in keys
        }

        # co-movement: pairs from PAIRS_UX whose deltas have the same sign
        co_movement: dict[str, bool] = {}
        for pair, (a, b) in PAIRS_UX.items():
            if a in delta_vals and b in delta_vals:
                same = (delta_vals[a] > 0 and delta_vals[b] > 0) or (
                    delta_vals[a] < 0 and delta_vals[b] < 0
                )
                co_movement[pair] = bool(same)

        composites = compute_per_tr_composites(values)

        frame = {
            "t_s": round(t * TR_DURATION, 3),
            "values": values,
            "deltas": delta_vals,
            "dominant": keys[dominant_idx[t]],
            "dominant_shift": bool(t > 0 and dominant_idx[t] != dominant_idx[t - 1]),
            "local_peak": local_peak,
            "spikes": spikes,
            "co_movement": co_movement,
            "composites": composites,
        }
        frames.append(frame)
    return frames


def _build_windows(
    roi_ts: Mapping[str, np.ndarray],
    *,
    window_trs: int,
    step_trs: int,
) -> list[dict]:
    """B2 — sliding window readings."""
    keys = list(roi_ts.keys())
    if not keys:
        return []
    n_trs = roi_ts[keys[0]].size
    if n_trs < max(4, window_trs):
        log.warning(
            "skipping window pass; not enough TRs (n=%d, window=%d)",
            n_trs, window_trs,
        )
        return []

    # Pre-stack matrix so we can re-slice per-window without repeated dict copies.
    matrix = np.stack([roi_ts[k] for k in keys], axis=1)

    # Pre-compute per-TR composites once for the whole run; we slice these.
    per_tr_composite_arrays: dict[str, np.ndarray] = {
        name: np.array([
            float(fn({k: float(matrix[t, i]) for i, k in enumerate(keys)}))
            for t in range(n_trs)
        ])
        for name, fn in PER_TR_COMPOSITES.items()
    }

    windows: list[dict] = []
    is_first = True
    for start in range(0, n_trs - window_trs + 1, step_trs):
        end = start + window_trs
        roi_window = {k: roi_ts[k][start:end] for k in keys}
        comp_window = {name: arr[start:end] for name, arr in per_tr_composite_arrays.items()}

        stats = {k: extract_stats(roi_window[k]) for k in keys}
        connectivity = compute_connectivity(roi_window)
        composites = compute_window_composites(
            comp_window,
            roi_window,
            is_first_window=is_first,
        )

        windows.append({
            "t_start_s": round(start * TR_DURATION, 3),
            "t_end_s": round(end * TR_DURATION, 3),
            "stats": stats,
            "connectivity": connectivity,
            "composites": composites,
        })
        is_first = False

    return windows


def build_timeline(
    roi_ts: Mapping[str, np.ndarray],
    *,
    window_trs: int = WINDOW_TRS_DEFAULT,
    step_trs: int = STEP_TRS_DEFAULT,
) -> dict:
    """Top-level entry point. Produces the full /process_video_timeline payload
    minus the `processing_time_ms` field (the API layer adds that)."""
    if not roi_ts:
        raise ValueError("roi_ts is empty — TRIBE returned no usable signal")

    # Soft check: every ROI must have the same length.
    lengths = {k: ts.size for k, ts in roi_ts.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"ROI timeseries length mismatch: {lengths}")

    n_trs = next(iter(lengths.values()))
    log.debug(
        "build_timeline begin",
        extra={"step": "timeline", "n_trs": n_trs,
               "window_trs": window_trs, "step_trs": step_trs},
    )

    frames = _build_per_tr_frames(roi_ts)
    windows = _build_windows(roi_ts, window_trs=window_trs, step_trs=step_trs)

    # Convenience: drop the per-ROI raw timeseries so the orchestrator
    # doesn't need to re-stack them. Ordered by ROI_KEYS so JSON column
    # order is stable.
    roi_series = {k: roi_ts[k].tolist() for k in ROI_KEYS if k in roi_ts}

    return {
        "n_trs": int(n_trs),
        "tr_duration_s": TR_DURATION,
        "frames": frames,
        "windows": windows,
        "window_config": {"window_trs": window_trs, "step_trs": step_trs},
        "roi_series": roi_series,
    }
