"""Per-ROI summary statistics computed over a window (or the whole timeseries).

DESIGN.md §5.3 Path A step 3: 11 features × 6 ROIs (we have 8 now).

The eleven features:
    peak           : max value in the window
    mean           : mean value
    auc            : trapezoidal integral
    onset_tr       : index of the first TR ≥ 0.5 × peak
    time_to_peak   : index of the peak TR
    rise_time      : time_to_peak - onset_tr
    rise_slope     : (peak - first_value) / max(time_to_peak, 1)
    fwhm           : full width at half max (TRs ≥ peak/2)
    sustained      : count of TRs ≥ peak * 0.7
    cv             : coefficient of variation (std / |mean|)
    decay_slope    : (last - peak) / max(n - time_to_peak - 1, 1)

These are also used by the windowed sub-pass in step7. For windows of <4
TRs many of these are mathematically empty — `extract_stats` clamps onset/
peak indices but the caller should respect `len(ts) > 3`.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


def extract_stats(ts: np.ndarray) -> dict[str, float]:
    """Compute the 11 stats for a single 1-D ROI timeseries.

    The returned dict is JSON-friendly (all values are Python floats / ints).
    """
    if ts.size == 0:
        return _zero_stats()

    arr = np.asarray(ts, dtype=np.float64)
    n = arr.size

    peak = float(arr.max())
    mean = float(arr.mean())
    auc = float(np.trapezoid(arr)) if n > 1 else float(arr.sum())

    # onset_tr — first index whose value crosses half the peak. If peak is
    # negative or zero the concept is moot; pick index 0.
    if peak > 0:
        threshold = 0.5 * peak
        above = np.where(arr >= threshold)[0]
        onset_tr = int(above[0]) if above.size else 0
    else:
        onset_tr = 0

    time_to_peak = int(np.argmax(arr))
    rise_time = max(time_to_peak - onset_tr, 0)

    rise_slope = (peak - float(arr[0])) / max(time_to_peak, 1)

    # fwhm — count of TRs whose value is ≥ peak / 2 (in the same sign as peak).
    if peak > 0:
        fwhm = int((arr >= peak / 2).sum())
    else:
        fwhm = 0

    sustained = int((arr >= peak * 0.7).sum()) if peak > 0 else 0

    std = float(arr.std())
    cv = float(std / abs(mean)) if abs(mean) > 1e-9 else 0.0

    decay_slope = (
        (float(arr[-1]) - peak) / max(n - time_to_peak - 1, 1)
        if time_to_peak < n - 1
        else 0.0
    )

    return {
        "peak": peak,
        "mean": mean,
        "auc": auc,
        "onset_tr": onset_tr,
        "time_to_peak": time_to_peak,
        "rise_time": rise_time,
        "rise_slope": float(rise_slope),
        "fwhm": fwhm,
        "sustained": sustained,
        "cv": cv,
        "decay_slope": float(decay_slope),
    }


def _zero_stats() -> dict[str, float]:
    return {
        "peak": 0.0, "mean": 0.0, "auc": 0.0, "onset_tr": 0,
        "time_to_peak": 0, "rise_time": 0, "rise_slope": 0.0,
        "fwhm": 0, "sustained": 0, "cv": 0.0, "decay_slope": 0.0,
    }
