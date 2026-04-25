"""Pearson connectivity for the 7 named PAIRS_UX.

DESIGN.md §5.16.5. Computed inside each window so the window response can
report relationships like "appeal_to_action correlation rose from 0.1 to 0.7."

Returns NaN for pairs whose window is constant (zero variance) — caller
formats this as 0.0 in JSON.
"""

from __future__ import annotations

import logging
from typing import Mapping

import numpy as np

from ..constants import PAIRS_UX

log = logging.getLogger(__name__)


def compute_connectivity(roi_ts: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Pearson r for each pair in PAIRS_UX, restricted to the ROIs present
    in `roi_ts`.

    Uses np.corrcoef with NaN guard. Window must be at least 2 TRs (Pearson
    is mathematically undefined at n=1; degenerate at n=2 but still defined).
    """
    out: dict[str, float] = {}
    for pair_name, (a, b) in PAIRS_UX.items():
        if a not in roi_ts or b not in roi_ts:
            log.debug("connectivity skipped: missing ROI for %s (%s/%s)", pair_name, a, b)
            continue
        x, y = roi_ts[a], roi_ts[b]
        if x.size < 2 or y.size < 2 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
            out[pair_name] = 0.0
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if not np.isfinite(r):
            r = 0.0
        out[pair_name] = r
    return out
