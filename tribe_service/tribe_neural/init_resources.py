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
    runner = TribeRunner()

    log.info(
        "load_resources done — %d masks, %d weight maps, vifs=%s, pines=%s",
        len(masks), len(weight_maps), vifs is not None, pines is not None,
    )

    return Resources(
        runner=runner,
        masks=masks,
        weight_maps=weight_maps,
        vifs=vifs,
        pines=pines,
        data_dir=data_dir,
    )
