"""Bake the Schaefer-400 parcel-to-vertex map onto fsaverage5.

Output:
    $TRIBE_DATA_DIR/schaefer400_parcels.npy   uint16 ndarray (NUM_VERTICES,)
        Each vertex (in fsaverage5 concatenated order: lh[0..10241], rh[10242..20483])
        carries a Schaefer parcel index in [0..400], where 0 = unassigned
        (rare; midline / corpus callosum cuts).

This is a ONE-TIME bake. The output ships in the Modal data volume next to
``data/masks/`` and ``data/neurosynth_weights.npz``. The TRIBE service loads
it at worker boot via ``init_resources.load_resources()``.

Why this exists:
    TRIBE v2 emits per-vertex predictions on fsaverage5 (20484 vertices).
    The frontend (BrainCortical.tsx) needs per-parcel activations (400 floats)
    to color a cortical mesh in real time. step2b_parcels.py performs the
    reduction at request time using this map. ASSUMPTIONS_BRAIN.md §1.2-§1.3.

Idempotency: outputs already on disk are preserved unless TRIBE_FORCE_REBUILD=1.

Usage:
    # Locally (dev):
    pip install nilearn nibabel  # already in requirements-tribe.txt
    TRIBE_DATA_DIR=./data python -m tribe_service.scripts.bake_parcel_map

    # Or directly:
    python tribe_service/scripts/bake_parcel_map.py

ASSUMPTIONS_BRAIN.md §1.2 explains the cross-hemisphere masking:
    Schaefer ships a single MNI152 volumetric atlas. When you project to the
    LEFT pial surface with vol_to_surf, vertices near the midline can pick
    up labels from the RIGHT hemisphere (because the volume doesn't know
    about hemispheres). We zero those out by parsing the label names
    (`7Networks_LH_*` vs `7Networks_RH_*`).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

# Make the sibling ``tribe_neural`` package importable when running this file
# directly (the parent of ``scripts/`` is the service root).
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
from tribe_neural.constants import NUM_VERTICES  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)5s] %(name)s :: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bake_parcel_map")


# ─── Constants ───────────────────────────────────────────────────────────────

#: Total Schaefer parcels. Hardcoded because the n_rois choice is part of
#: the design contract — switching to 100 or 1000 would force a frontend
#: re-bake too.
N_PARCELS: int = 400

#: Yeo network count (7 vs 17). 7 keeps labels human-readable.
YEO_NETWORKS: int = 7

#: Per-hemisphere vertex count for fsaverage5. ASSUMPTIONS_BRAIN.md §1.1.
N_VERTICES_PER_HEMI: int = 10242


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    """Resolve the data directory the same way init_resources does."""
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    log.info("data_dir resolved to %s", p)
    return p


def _force_rebuild() -> bool:
    return os.getenv("TRIBE_FORCE_REBUILD", "0").lower() in ("1", "true", "yes")


def _decode(label) -> str:
    """Schaefer LUT may return labels as bytes (numpy structured array
    quirk). Normalize to str."""
    if isinstance(label, (bytes, bytearray)):
        return label.decode("utf-8")
    return str(label)


# ─── Main ────────────────────────────────────────────────────────────────────

def bake_parcel_map(data_dir: Path) -> np.ndarray:
    """Project Schaefer-400 onto fsaverage5 and return the per-vertex map.

    Returns a uint16 array of shape ``(NUM_VERTICES,)`` with values in
    ``[0, N_PARCELS]``. Saves to ``data_dir/schaefer400_parcels.npy``.

    Raises:
        RuntimeError: if any sanity check fails (per-hemisphere coverage
        below 90%, Schaefer LUT missing expected columns, vertex count
        mismatch). Loud failure — never silently emit a degenerate map.
    """
    out_path = data_dir / "schaefer400_parcels.npy"
    if out_path.exists() and not _force_rebuild():
        log.info("parcel map already exists at %s — skipping bake "
                 "(set TRIBE_FORCE_REBUILD=1 to rebuild)", out_path)
        cached = np.load(out_path)
        if cached.shape != (NUM_VERTICES,):
            raise RuntimeError(
                f"cached parcel map at {out_path} has bad shape {cached.shape}; "
                f"expected ({NUM_VERTICES},). Delete the file and re-run."
            )
        return cached

    from nilearn import datasets, surface  # type: ignore  # noqa: WPS433

    # ── 1. Fetch Schaefer atlas (MNI volumetric) ────────────────────────────
    log.info("fetching Schaefer-2018 atlas (n_rois=%d, yeo=%d)",
             N_PARCELS, YEO_NETWORKS)
    t0 = time.perf_counter()
    atlas = datasets.fetch_atlas_schaefer_2018(
        n_rois=N_PARCELS,
        yeo_networks=YEO_NETWORKS,
        resolution_mm=1,
    )
    log.info("Schaefer fetched in %.1fs (maps=%s)",
             time.perf_counter() - t0, atlas["maps"])

    labels = [_decode(b) for b in atlas["labels"]]
    log.info("Schaefer label count: %d (expected %d). Sample: %s",
             len(labels), N_PARCELS, labels[:3])
    if len(labels) != N_PARCELS:
        raise RuntimeError(
            f"Schaefer returned {len(labels)} labels; expected {N_PARCELS}. "
            "Atlas version mismatch?"
        )

    # ── 2. Build hemisphere masks from label names ──────────────────────────
    # Labels look like "7Networks_LH_Vis_1" or "7Networks_RH_Default_PCC_2".
    # Index into `labels` is 0-based; Schaefer's volumetric values are 1-based
    # (0 = background). So labels[i] corresponds to volumetric value (i+1).
    is_lh = np.array(["_LH_" in name for name in labels], dtype=bool)
    is_rh = np.array(["_RH_" in name for name in labels], dtype=bool)
    n_lh = int(is_lh.sum())
    n_rh = int(is_rh.sum())
    log.info("hemisphere split: %d LH, %d RH parcels (sum=%d, expected %d)",
             n_lh, n_rh, n_lh + n_rh, N_PARCELS)
    if n_lh + n_rh != N_PARCELS:
        raise RuntimeError(
            f"Schaefer label hemisphere parsing failed: {n_lh}+{n_rh} != {N_PARCELS}. "
            f"Sample labels: {labels[:5]}"
        )

    # ── 3. Fetch fsaverage5 surface ─────────────────────────────────────────
    log.info("fetching fsaverage5 mesh")
    t1 = time.perf_counter()
    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")
    log.info("fsaverage5 fetched in %.1fs", time.perf_counter() - t1)

    # ── 4. Project atlas to surface (per-hemi, nearest interpolation) ───────
    log.info("projecting Schaefer volume to fsaverage5 surface (nearest interp)")
    t2 = time.perf_counter()
    lh_proj = surface.vol_to_surf(
        atlas["maps"], fsavg["pial_left"],
        interpolation="nearest", radius=1.0,
    )
    rh_proj = surface.vol_to_surf(
        atlas["maps"], fsavg["pial_right"],
        interpolation="nearest", radius=1.0,
    )
    log.info("projection done in %.1fs (lh=%s, rh=%s)",
             time.perf_counter() - t2, lh_proj.shape, rh_proj.shape)

    # vol_to_surf returns float; cast to uint16 (safe — Schaefer values are 0..400).
    lh_int = np.rint(lh_proj).astype(np.int64)
    rh_int = np.rint(rh_proj).astype(np.int64)

    if lh_int.shape != (N_VERTICES_PER_HEMI,):
        raise RuntimeError(
            f"LH projection shape {lh_int.shape}; expected ({N_VERTICES_PER_HEMI},). "
            "fsaverage5 download corrupted?"
        )
    if rh_int.shape != (N_VERTICES_PER_HEMI,):
        raise RuntimeError(
            f"RH projection shape {rh_int.shape}; expected ({N_VERTICES_PER_HEMI},)."
        )

    # ── 5. Cross-hemisphere masking ──────────────────────────────────────────
    # Volumetric label v maps to labels[v-1]. We want LH mesh to only carry
    # LH parcel labels, and likewise for RH. Anything else → 0 (unassigned).
    lh_clean = np.zeros(N_VERTICES_PER_HEMI, dtype=np.uint16)
    rh_clean = np.zeros(N_VERTICES_PER_HEMI, dtype=np.uint16)
    for v_idx in range(N_VERTICES_PER_HEMI):
        lh_v = lh_int[v_idx]
        if 1 <= lh_v <= N_PARCELS and is_lh[lh_v - 1]:
            lh_clean[v_idx] = lh_v
        rh_v = rh_int[v_idx]
        if 1 <= rh_v <= N_PARCELS and is_rh[rh_v - 1]:
            rh_clean[v_idx] = rh_v

    lh_coverage = float(np.count_nonzero(lh_clean)) / N_VERTICES_PER_HEMI
    rh_coverage = float(np.count_nonzero(rh_clean)) / N_VERTICES_PER_HEMI
    log.info("per-hemi coverage after masking: lh=%.1f%%, rh=%.1f%%",
             lh_coverage * 100, rh_coverage * 100)
    if lh_coverage < 0.85 or rh_coverage < 0.85:
        raise RuntimeError(
            f"Per-hemisphere parcel coverage too low (lh={lh_coverage:.2%}, "
            f"rh={rh_coverage:.2%}). Expected >= 85%. Atlas projection broke."
        )

    # ── 6. Concatenate to fsaverage5 layout (lh first, rh second) ───────────
    parcels = np.concatenate([lh_clean, rh_clean]).astype(np.uint16)
    if parcels.shape != (NUM_VERTICES,):
        raise RuntimeError(
            f"concatenated parcel map shape {parcels.shape}; "
            f"expected ({NUM_VERTICES},)"
        )

    n_unique = len(np.unique(parcels))  # includes 0
    log.info("final map: shape=%s dtype=%s, unique parcels=%d (expected %d incl. 0)",
             parcels.shape, parcels.dtype, n_unique, N_PARCELS + 1)

    # ── 7. Save ──────────────────────────────────────────────────────────────
    np.save(out_path, parcels)
    size_kb = out_path.stat().st_size / 1024.0
    log.info("wrote %s (%.1f KB)", out_path, size_kb)

    return parcels


def main() -> None:
    log.info("==== bake_parcel_map start ====")
    data_dir = _data_dir()
    bake_parcel_map(data_dir)
    log.info("==== bake_parcel_map done ====")


if __name__ == "__main__":
    main()
