"""One-time-per-worker resource loading.

Builds the dataclass `Resources` that every inference call reads:
    - `runner`        : TribeRunner
    - `masks`         : dict[Yeo network substring -> bool ndarray (NUM_VERTICES,)]
    - `weight_maps`   : dict[Neurosynth term -> float ndarray (NUM_VERTICES,)]
    - `vifs`          : ndarray (NUM_VERTICES,) | None
    - `pines`         : ndarray (NUM_VERTICES,) | None

Heavy work (Schaefer atlas projection, Neurosynth meta-analysis,
VIFS/PINES projection) is done by the scripts in `scripts/` and CACHED
to `TRIBE_DATA_DIR`. This module just loads the caches.

DESIGN.md §5.5 + §5.8.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np

from .constants import (
    NEUROSYNTH_TERMS,
    NUM_VERTICES,
    YEO_NETWORK_SUBSTRINGS,
)
from .tribe_runner import TribeRunner

log = logging.getLogger(__name__)


@dataclass
class Resources:
    runner: TribeRunner
    masks: Mapping[str, np.ndarray]
    weight_maps: Mapping[str, np.ndarray]
    vifs: np.ndarray | None = None
    pines: np.ndarray | None = None
    #: Schaefer-400 parcel index per fsaverage5 vertex.
    #: Shape (NUM_VERTICES,) uint16 with values in [0, 400]; 0 = unassigned.
    #: Optional: if the bake script hasn't been run yet, the cortical brain
    #: visualization gracefully degrades to the placeholder. The chart and
    #: ROI pipeline don't use this. See ASSUMPTIONS_BRAIN.md §1.2.
    parcels: np.ndarray | None = None
    data_dir: Path | None = None
    extra: dict = field(default_factory=dict)


def _data_dir() -> Path:
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_masks(data_dir: Path) -> dict[str, np.ndarray]:
    """Load Schaefer-built network masks from disk.

    Resources are produced by `scripts/generate_weights.py` and
    `scripts/project_signatures.py` (per DESIGN.md §5.8). Each Yeo network
    substring gets one boolean mask file: `masks/<substring>.npy`.
    """
    masks_dir = data_dir / "masks"
    if not masks_dir.exists():
        raise RuntimeError(
            f"masks directory missing: {masks_dir}. "
            "Run scripts/generate_weights.py to populate the cache. "
            "See DESIGN.md §5.8."
        )
    out: dict[str, np.ndarray] = {}
    for net in YEO_NETWORK_SUBSTRINGS:
        path = masks_dir / f"{net}.npy"
        if not path.exists():
            raise RuntimeError(
                f"mask missing: {path}. "
                "Run scripts/generate_weights.py to populate the cache."
            )
        arr = np.load(path)
        if arr.shape != (NUM_VERTICES,):
            raise RuntimeError(
                f"mask {path} has bad shape {arr.shape}; "
                f"expected ({NUM_VERTICES},)"
            )
        out[net] = arr.astype(bool)
    return out


def _load_weight_maps(data_dir: Path) -> dict[str, np.ndarray]:
    path = data_dir / "neurosynth_weights.npz"
    if not path.exists():
        raise RuntimeError(
            f"neurosynth weights missing: {path}. "
            "Run scripts/generate_weights.py to populate the cache."
        )
    data = np.load(path)
    out: dict[str, np.ndarray] = {}
    for term in NEUROSYNTH_TERMS:
        if term not in data:
            raise RuntimeError(
                f"neurosynth weight map for term '{term}' missing in {path}"
            )
        out[term] = data[term].astype(np.float64)
    return out


def _try_load_signature(data_dir: Path, name: str) -> np.ndarray | None:
    path = data_dir / f"{name}_surface.npy"
    if not path.exists():
        return None
    arr = np.load(path)
    if arr.shape != (NUM_VERTICES,):
        log.error("signature %s has bad shape %s", path, arr.shape)
        return None
    return arr


def _try_load_parcel_map(data_dir: Path) -> np.ndarray | None:
    """Load the Schaefer-400 parcel-to-vertex map produced by
    ``scripts/bake_parcel_map.py``.

    Optional: if the bake hasn't been run, returns None and the cortical
    brain visualization gracefully degrades to the placeholder geometry.
    Loud logging in both the success and absence cases so it's obvious
    from the boot logs whether the brain rendering will work at runtime.
    See ASSUMPTIONS_BRAIN.md §1.2.
    """
    path = data_dir / "schaefer400_parcels.npy"
    if not path.exists():
        log.warning(
            "parcel map not found at %s — cortical brain rendering will "
            "fall back to the placeholder. Run "
            "`python -m tribe_service.scripts.bake_parcel_map` to bake it.",
            path,
        )
        return None
    arr = np.load(path)
    if arr.shape != (NUM_VERTICES,):
        log.error(
            "parcel map at %s has bad shape %s; expected (%d,). "
            "Refusing to load — cortical rendering will degrade. "
            "Re-run `python -m tribe_service.scripts.bake_parcel_map`.",
            path, arr.shape, NUM_VERTICES,
        )
        return None
    if arr.dtype != np.uint16:
        log.warning(
            "parcel map dtype is %s; expected uint16. Casting at load time.",
            arr.dtype,
        )
        arr = arr.astype(np.uint16)
    n_unique = int(np.unique(arr).size)
    log.info(
        "parcel map loaded from %s (shape=%s, unique=%d)",
        path, arr.shape, n_unique,
    )
    return arr


def load_resources() -> Resources:
    """Build the per-worker `Resources` once."""
    data_dir = _data_dir()
    log.info(
        "load_resources begin (data_dir=%s)",
        data_dir,
        extra={"step": "init", "data_dir": str(data_dir)},
    )

    masks = _load_masks(data_dir)
    weight_maps = _load_weight_maps(data_dir)
    vifs = _try_load_signature(data_dir, "vifs")
    pines = _try_load_signature(data_dir, "pines")
    parcels = _try_load_parcel_map(data_dir)
    runner = TribeRunner()

    log.info(
        "load_resources done — %d masks, %d weight maps, vifs=%s, pines=%s, parcels=%s",
        len(masks), len(weight_maps),
        vifs is not None, pines is not None, parcels is not None,
    )

    return Resources(
        runner=runner,
        masks=masks,
        weight_maps=weight_maps,
        vifs=vifs,
        pines=pines,
        parcels=parcels,
        data_dir=data_dir,
    )
