"""ROI extraction.

Take the (n_TRs, 20484) per-vertex prediction tensor from TRIBE and reduce
it to a dict of named 1-D timeseries — one per UX ROI.

Algorithm (DESIGN.md §5.16.2 / §5.16.7):
1. For each ROI in NETWORK_KEYS_UX, gather the boolean masks for every
   listed Yeo network. OR them together.
2. For each (network, weight_term) tuple, multiply the network mask by the
   per-vertex Neurosynth weight if `weight_term` is set; uniform weight
   otherwise.
3. Sum the resulting per-vertex weight vector. The ROI activation at TR `t`
   is `(preds[t] * w).sum() / w.sum()`.
4. Apply two post-extraction hooks the dict can't express:
   - `visual_fluency`: subtract 0.5 × (DorsAttn-mean activation per TR)
     (DESIGN.md §5.16.2 caption + §5.16.7).
5. Z-score each ROI timeseries across time so downstream composite
   formulas (which mix ROIs of wildly different baselines) compare apples
   to apples.

The visual_fluency hook is computed BEFORE z-scoring because the published
formula (Reber 2004 / DESIGN.md §5.16.2 row 2) operates on raw signal
magnitudes; only the post-hook output is normalized.
"""

from __future__ import annotations

import logging
from typing import Mapping

import numpy as np

from ..constants import NETWORK_KEYS_UX, ROI_KEYS

log = logging.getLogger(__name__)


def _zscore(arr: np.ndarray) -> np.ndarray:
    """Per-axis z-score with a tiny epsilon to avoid divide-by-zero."""
    mean = arr.mean()
    std = arr.std()
    if std < 1e-9:
        return arr - mean
    return (arr - mean) / std


def _network_mean_per_tr(
    preds: np.ndarray,
    masks: Mapping[str, np.ndarray],
    network_substring: str,
) -> np.ndarray:
    """Mean activation across the vertices belonging to a Yeo network, per TR.

    Used by the post-extraction visual_fluency hook.
    """
    mask = masks.get(network_substring)
    if mask is None or not mask.any():
        log.warning("network mask missing or empty: %s", network_substring)
        return np.zeros(preds.shape[0])
    return preds[:, mask].mean(axis=1)


def extract_all(
    preds: np.ndarray,
    masks: Mapping[str, np.ndarray],
    weight_maps: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Reduce per-vertex predictions to per-ROI timeseries.

    Args:
        preds: shape (n_TRs, n_vertices). Output of TRIBE.
        masks: Yeo network substring -> boolean vertex mask of shape (n_vertices,).
            E.g., masks["_Default_"] has True at every vertex assigned to DMN.
        weight_maps: Neurosynth term -> non-negative float weight per vertex,
            shape (n_vertices,). E.g., weight_maps["fear"].

    Returns:
        Dict keyed by ROI name (in ROI_KEYS order). Each value is a z-scored
        1-D float array of shape (n_TRs,).
    """
    if preds.ndim != 2:
        raise ValueError(f"preds must be 2-D (n_TRs, n_vertices); got {preds.shape}")
    n_trs, n_vertices = preds.shape
    log.debug("extract_all begin", extra={
        "step": "roi", "n_trs": n_trs, "n_vertices": n_vertices,
    })

    out: dict[str, np.ndarray] = {}
    for roi_name in ROI_KEYS:
        spec = NETWORK_KEYS_UX[roi_name]
        # Build a per-vertex weight vector by summing each (network, term)
        # contribution. This lets one ROI span multiple networks (e.g.
        # aesthetic_appeal := DMN-by-memory + Limbic-by-reward).
        w = np.zeros(n_vertices, dtype=np.float64)
        for net_substring, weight_term in spec:
            mask = masks.get(net_substring)
            if mask is None:
                log.warning("missing mask for network %s (roi=%s) — skipping",
                            net_substring, roi_name)
                continue
            term_w = weight_maps.get(weight_term) if weight_term else None
            if term_w is not None:
                contribution = mask.astype(np.float64) * term_w
            else:
                contribution = mask.astype(np.float64)
            w += contribution

        denom = w.sum()
        if denom < 1e-9:
            # Empty ROI — emit zeros rather than NaN. Won't trigger spikes
            # but won't crash either. extract_resources should ensure this
            # doesn't happen for any of the 8 keyset ROIs.
            log.error("ROI %s has zero total weight; emitting zeros", roi_name)
            ts = np.zeros(n_trs, dtype=np.float64)
        else:
            # (n_trs, n_vertices) @ (n_vertices,) -> (n_trs,)
            ts = (preds @ w) / denom
        out[roi_name] = ts

    # Post-extraction hook: visual_fluency = _Vis_ − 0.5 × _DorsAttn_
    # (DESIGN.md §5.16.7). The two-pass form is needed because the dict
    # entry for visual_fluency only carries the _Vis_ leg.
    if "visual_fluency" in out:
        dors_attn_mean = _network_mean_per_tr(preds, masks, "_DorsAttn_")
        out["visual_fluency"] = out["visual_fluency"] - 0.5 * dors_attn_mean

    # Z-score every ROI so downstream composite weights are commensurate.
    for roi_name, ts in out.items():
        out[roi_name] = _zscore(ts)

    log.debug("extract_all done — %d ROIs", len(out))
    return out
