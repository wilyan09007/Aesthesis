"""One-time-per-worker resource loading.

Builds the dataclass `Resources` that every inference call reads:
    - `runner`        : TribeRunner (real or mock)
    - `masks`         : dict[Yeo network substring -> bool ndarray (NUM_VERTICES,)]
    - `weight_maps`   : dict[Neurosynth term -> float ndarray (NUM_VERTICES,)]
    - `vifs`          : ndarray (NUM_VERTICES,) | None
    - `pines`         : ndarray (NUM_VERTICES,) | None

Heavy work (Schaefer atlas projection, Neurosynth meta-analysis,
VIFS/PINES projection) is done by the scripts in `scripts/` and CACHED
to `TRIBE_DATA_DIR`. This module just loads the caches.

Mock mode (`TRIBE_MOCK_MODE=1`) skips the real model load and synthesizes
plausible masks + weight maps so the rest of the pipeline can run on a
laptop without nilearn / nibabel installed.

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
from .tribe_runner import TribeRunner, build_runner

log = logging.getLogger(__name__)


@dataclass
class Resources:
    runner: TribeRunner
    masks: Mapping[str, np.ndarray]
    weight_maps: Mapping[str, np.ndarray]
    vifs: np.ndarray | None = None
    pines: np.ndarray | None = None
    data_dir: Path | None = None
    mock_mode: bool = False
    extra: dict = field(default_factory=dict)


def _data_dir() -> Path:
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _synthesize_mock_masks(seed: int = 42) -> dict[str, np.ndarray]:
    """Generate plausible-looking Yeo network masks for mock mode.

    Each network gets a random subset of the cortical mesh, deterministic
    given the seed. Sizes roughly match published Yeo7 partition fractions
    so per-ROI weight sums are non-trivial.
    """
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


def _synthesize_mock_weight_maps(seed: int = 137) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    for term in NEUROSYNTH_TERMS:
        # Right-skewed non-negative weights (Neurosynth ALE looks like this).
        w = rng.gamma(shape=2.0, scale=0.3, size=NUM_VERTICES).astype(np.float64)
        out[term] = w
    return out


def _try_load_real_masks(data_dir: Path) -> dict[str, np.ndarray] | None:
    """Try to load Schaefer-built network masks from disk.

    Real-mode resources are produced by `scripts/generate_weights.py` and
    `scripts/project_signatures.py` (per DESIGN.md §5.8). Each Yeo network
    substring gets one boolean mask file: `masks/<substring>.npy`.

    Returns None if any expected file is missing — caller falls back to mock.
    """
    masks_dir = data_dir / "masks"
    if not masks_dir.exists():
        return None
    out: dict[str, np.ndarray] = {}
    for net in YEO_NETWORK_SUBSTRINGS:
        path = masks_dir / f"{net}.npy"
        if not path.exists():
            log.warning("real mask missing: %s — will fall back to mock", path)
            return None
        arr = np.load(path)
        if arr.shape != (NUM_VERTICES,):
            log.error("mask %s has bad shape %s; expected (%d,)",
                      path, arr.shape, NUM_VERTICES)
            return None
        out[net] = arr.astype(bool)
    return out


def _try_load_real_weight_maps(data_dir: Path) -> dict[str, np.ndarray] | None:
    path = data_dir / "neurosynth_weights.npz"
    if not path.exists():
        return None
    data = np.load(path)
    out: dict[str, np.ndarray] = {}
    for term in NEUROSYNTH_TERMS:
        if term not in data:
            log.warning("neurosynth weight map for %s missing in %s", term, path)
            return None
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


def load_resources(force_mock: bool | None = None) -> Resources:
    """Build the per-worker `Resources` once.

    `force_mock`:
        - None  : honor `TRIBE_MOCK_MODE` env var (default: real)
        - True  : always use mock
        - False : try real, raise if any artifact is missing
    """
    mock_env = os.getenv("TRIBE_MOCK_MODE", "0").lower() in ("1", "true", "yes")
    mock = mock_env if force_mock is None else force_mock

    data_dir = _data_dir()
    log.info(
        "load_resources begin (mock=%s, data_dir=%s)",
        mock, data_dir,
        extra={"step": "init", "mock": mock, "data_dir": str(data_dir)},
    )

    if mock:
        masks = _synthesize_mock_masks()
        weight_maps = _synthesize_mock_weight_maps()
        vifs = None
        pines = None
    else:
        masks = _try_load_real_masks(data_dir)
        weight_maps = _try_load_real_weight_maps(data_dir)
        if masks is None or weight_maps is None:
            raise RuntimeError(
                "real-mode resources missing under "
                f"{data_dir}. Either set TRIBE_MOCK_MODE=1, or run "
                "scripts/generate_weights.py and scripts/project_signatures.py "
                "to populate the cache. See DESIGN.md §5.8."
            )
        vifs = _try_load_signature(data_dir, "vifs")
        pines = _try_load_signature(data_dir, "pines")

    runner = build_runner(mock=mock)

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
        mock_mode=mock,
    )
