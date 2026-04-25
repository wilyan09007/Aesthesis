"""Build cached resources required by the TRIBE service worker.

Outputs (under ``$TRIBE_DATA_DIR``, default ``./data``):

    masks/{network_substring}.npy   — bool ndarray (NUM_VERTICES,)   × 7 Yeo nets
    neurosynth_weights.npz          — float64 ndarray (NUM_VERTICES,) × 7 terms

Heavy work, ~30–60 minutes on CPU. Idempotent: outputs that already exist are
preserved unless ``TRIBE_FORCE_REBUILD=1``. Verbose logging at every step
(atlas fetch, label parsing, surface projection, kernel timing, output stats)
so failures surface clearly when the next worker tries to load the cache.

DESIGN.md §5.5, §5.8, §5.16. ASSUMPTIONS.md §3, §4 explain why this lives
outside the request path: it's GPU-irrelevant, network-heavy, and only has
to run once per fresh data volume.

Usage:
    TRIBE_DATA_DIR=/app/data python -m scripts.generate_weights
or directly:
    python tribe_service/scripts/generate_weights.py
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
from tribe_neural.constants import (  # noqa: E402
    NEUROSYNTH_TERMS,
    NUM_VERTICES,
    YEO_NETWORK_SUBSTRINGS,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)5s] %(name)s :: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("generate_weights")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    log.info("data_dir resolved to %s", p)
    return p


def _force_rebuild() -> bool:
    return os.getenv("TRIBE_FORCE_REBUILD", "0").lower() in ("1", "true", "yes")


def _decode(label) -> str:
    return label.decode("utf-8") if isinstance(label, (bytes, bytearray)) else str(label)


# ─── 1. Schaefer 400 → fsaverage5 → boolean per-network masks ────────────────

def _build_schaefer_masks(data_dir: Path) -> dict[str, np.ndarray]:
    from nilearn import datasets, surface  # type: ignore

    masks_dir = data_dir / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    if not _force_rebuild() and all(
        (masks_dir / f"{n}.npy").exists() for n in YEO_NETWORK_SUBSTRINGS
    ):
        log.info(
            "all %d masks already present in %s — skipping Schaefer build "
            "(set TRIBE_FORCE_REBUILD=1 to override)",
            len(YEO_NETWORK_SUBSTRINGS), masks_dir,
        )
        return {n: np.load(masks_dir / f"{n}.npy") for n in YEO_NETWORK_SUBSTRINGS}

    log.info("fetching Schaefer 2018 atlas (n_rois=400, yeo_networks=7)")
    t0 = time.perf_counter()
    atlas = datasets.fetch_atlas_schaefer_2018(
        n_rois=400, yeo_networks=7, resolution_mm=1
    )
    log.info(
        "schaefer fetched in %.1fs — maps=%s, n_labels=%d",
        time.perf_counter() - t0, atlas["maps"], len(atlas["labels"]),
    )

    labels = [_decode(l) for l in atlas["labels"]]
    log.info("first 3 labels: %r", labels[:3])
    log.info("last 3 labels:  %r", labels[-3:])

    # nilearn changed this between releases:
    #  - <0.13: labels is 400-long (no Background); volume value k -> labels[k-1]
    #  - >=0.13: labels is 401-long with labels[0]=='Background'; volume value k -> labels[k]
    # Pick the right mapping defensively.
    if labels and labels[0].strip().lower() == "background":
        log.info(
            "labels has leading 'Background' (n=%d) — using direct mapping "
            "volume_value[k] -> labels[k]",
            len(labels),
        )
        label_for_index = {i: lbl for i, lbl in enumerate(labels)}
    elif len(labels) == 400:
        log.info(
            "labels has no Background entry (n=400) — using shifted mapping "
            "volume_value[k] -> labels[k-1]"
        )
        label_for_index = {i + 1: lbl for i, lbl in enumerate(labels)}
    else:
        log.warning(
            "unexpected labels structure (n=%d, first=%r) — falling back to "
            "direct mapping; verify mask coverage",
            len(labels), labels[:1],
        )
        label_for_index = {i: lbl for i, lbl in enumerate(labels)}

    log.info("fetching fsaverage5 mesh")
    t1 = time.perf_counter()
    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")
    log.info("fsaverage5 fetched in %.1fs", time.perf_counter() - t1)

    log.info(
        "projecting Schaefer label volume to fsaverage5 surface "
        "(interpolation=nearest_most_frequent)"
    )
    t2 = time.perf_counter()
    lh = surface.vol_to_surf(
        atlas["maps"], fsavg["pial_left"],
        interpolation="nearest_most_frequent", radius=3.0,
    )
    log.info(
        "  left  hemi → shape=%s dtype=%s n_unique=%d in %.1fs",
        lh.shape, lh.dtype, len(np.unique(lh)), time.perf_counter() - t2,
    )
    t3 = time.perf_counter()
    rh = surface.vol_to_surf(
        atlas["maps"], fsavg["pial_right"],
        interpolation="nearest_most_frequent", radius=3.0,
    )
    log.info(
        "  right hemi → shape=%s n_unique=%d in %.1fs",
        rh.shape, len(np.unique(rh)), time.perf_counter() - t3,
    )

    expected_per_hemi = NUM_VERTICES // 2
    if lh.shape != (expected_per_hemi,) or rh.shape != (expected_per_hemi,):
        raise RuntimeError(
            f"hemi shapes {lh.shape}/{rh.shape} disagree with expected "
            f"({expected_per_hemi},) — fsaverage5 should be 10242 verts/hemi"
        )

    surface_labels = np.concatenate([lh, rh]).astype(int)
    n_bg = int((surface_labels == 0).sum())
    log.info(
        "combined surface labels: shape=%s n_unique=%d n_background=%d (%.1f%%)",
        surface_labels.shape, len(np.unique(surface_labels)),
        n_bg, 100 * n_bg / surface_labels.size,
    )

    masks: dict[str, np.ndarray] = {}
    for net in YEO_NETWORK_SUBSTRINGS:
        parcel_indices = [
            idx for idx, lbl in label_for_index.items() if net in lbl
        ]
        log.info(
            "network %-15s → %3d parcels match (sample idx=%s)",
            net, len(parcel_indices), parcel_indices[:5],
        )
        if not parcel_indices:
            log.error("0 parcels match %s — sample labels: %r", net, labels[:8])
            raise RuntimeError(f"no parcels match network substring {net}")

        mask = np.isin(surface_labels, parcel_indices)
        log.info(
            "                   → vertices: %d/%d True (%.1f%%)",
            int(mask.sum()), mask.size, 100.0 * float(mask.mean()),
        )
        out = masks_dir / f"{net}.npy"
        np.save(out, mask)
        log.info("                   → saved %s (%d bytes)", out, out.stat().st_size)
        masks[net] = mask

    return masks


# ─── 2. Neurosynth term-association maps → fsaverage5 → .npz ─────────────────

def _resolve_term_label(term: str, columns: list[str]) -> str:
    """Match a bare term ('fear') to a NiMARE annotation column.

    Modern Neurosynth dumps prefix labels like ``terms_abstract_tfidf__fear``.
    Older dumps use ``Neurosynth_TFIDF__fear``. We accept any column ending
    with ``__<term>``, preferring tfidf variants, then alphabetical.
    """
    candidates = [c for c in columns if c == term or c.endswith(f"__{term}")]
    if not candidates:
        raise RuntimeError(
            f"no annotation column matches term '{term}' "
            f"(checked {len(columns)} columns; sample: {columns[:5]!r})"
        )
    tfidf = sorted(c for c in candidates if "tfidf" in c.lower())
    chosen = tfidf[0] if tfidf else sorted(candidates)[0]
    log.info(
        "  term=%-12s → label=%-40s (%d candidate(s))",
        term, chosen, len(candidates),
    )
    return chosen


def _build_neurosynth_weights(data_dir: Path) -> dict[str, np.ndarray]:
    from nilearn import datasets, surface  # type: ignore
    from nimare.extract import fetch_neurosynth  # type: ignore
    from nimare.io import convert_neurosynth_to_dataset  # type: ignore
    from nimare.meta.cbma.mkda import MKDADensity  # type: ignore

    out_path = data_dir / "neurosynth_weights.npz"
    if out_path.exists() and not _force_rebuild():
        log.info(
            "neurosynth weights already present at %s (%d bytes) — skipping",
            out_path, out_path.stat().st_size,
        )
        with np.load(out_path) as data:
            return {term: data[term].astype(np.float64) for term in NEUROSYNTH_TERMS}

    ns_cache = data_dir / "neurosynth"
    ns_cache.mkdir(parents=True, exist_ok=True)
    log.info("downloading Neurosynth corpus (~1 GB) → %s", ns_cache)
    t0 = time.perf_counter()
    # NiMARE's fetch_neurosynth default returns a list of `Studyset` objects;
    # convert_neurosynth_to_dataset wants raw file paths instead. Asking for
    # `return_type="files"` gives a list of dicts shaped:
    #   [{"coordinates": <path>, "metadata": <path>, "features": [<path>]}]
    files = fetch_neurosynth(
        data_dir=str(ns_cache),
        version="7",
        overwrite=False,
        source="abstract",
        vocab="terms",
        return_type="files",
    )
    log.info("fetch_neurosynth done in %.1fs", time.perf_counter() - t0)

    # Take the first dataset in the returned list.
    if isinstance(files, list) and files:
        files = files[0]
    if not isinstance(files, dict):
        raise RuntimeError(
            "fetch_neurosynth returned an unexpected shape — expected a "
            f"dict of file paths, got {type(files).__name__}. Check whether "
            "the NiMARE API surface changed again."
        )
    log.info("neurosynth payload keys: %s", list(files.keys()))

    log.info("building NiMARE Dataset from coordinates+metadata+features")
    t1 = time.perf_counter()
    dset = convert_neurosynth_to_dataset(
        coordinates_file=files["coordinates"],
        metadata_file=files["metadata"],
        annotations_files=files["features"],
    )
    log.info(
        "Dataset built in %.1fs — n_studies=%d n_annotation_cols=%d",
        time.perf_counter() - t1, len(dset.ids), dset.annotations.shape[1],
    )

    columns = list(dset.annotations.columns)
    log.info("first 6 annotation columns: %r", columns[:6])

    log.info("fetching fsaverage5 mesh for surface projection")
    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")

    weight_maps: dict[str, np.ndarray] = {}
    for term in NEUROSYNTH_TERMS:
        log.info("─── term: %s ───", term)
        label = _resolve_term_label(term, columns)
        ids = dset.get_studies_by_label(labels=[label], label_threshold=0.001)
        log.info("  → %d studies match (threshold=0.001)", len(ids))
        if len(ids) < 20:
            log.warning(
                "  term=%s has only %d studies — z-map will be very noisy",
                term, len(ids),
            )
        if not ids:
            log.error("  term=%s has 0 studies, skipping; weight will be zeros", term)
            weight_maps[term] = np.zeros(NUM_VERTICES, dtype=np.float64)
            continue

        sub = dset.slice(ids)
        log.info("  → fitting MKDADensity (kernel__r=10, null=approximate)")
        t = time.perf_counter()
        mkda = MKDADensity(kernel__r=10, null_method="approximate")
        result = mkda.fit(sub)
        log.info("  → MKDA fit done in %.1fs", time.perf_counter() - t)

        z_img = result.get_map("z")
        z_data = np.asarray(z_img.get_fdata())
        log.info(
            "  → z-volume: shape=%s min=%.3f max=%.3f n_pos_voxels=%d",
            z_data.shape, float(z_data.min()), float(z_data.max()),
            int((z_data > 0).sum()),
        )

        log.info("  → projecting z-volume to fsaverage5 (linear interp)")
        t2 = time.perf_counter()
        lh = surface.vol_to_surf(z_img, fsavg["pial_left"], interpolation="linear")
        rh = surface.vol_to_surf(z_img, fsavg["pial_right"], interpolation="linear")
        weight = np.concatenate([lh, rh]).astype(np.float64)
        # Zero out NaNs from out-of-mask vertices and floor at 0 — the
        # downstream linear combination expects non-negative weights.
        weight = np.nan_to_num(weight, nan=0.0, posinf=0.0, neginf=0.0)
        weight = np.maximum(weight, 0.0)
        log.info(
            "  → surface map: shape=%s n_pos=%d max=%.3f mean=%.3f in %.1fs",
            weight.shape, int((weight > 0).sum()),
            float(weight.max()), float(weight.mean()),
            time.perf_counter() - t2,
        )
        if weight.shape != (NUM_VERTICES,):
            raise RuntimeError(
                f"unexpected projected shape {weight.shape} for term {term}"
            )
        weight_maps[term] = weight

    log.info("saving %d weight maps to %s", len(weight_maps), out_path)
    np.savez(out_path, **weight_maps)
    log.info("npz size: %d bytes", out_path.stat().st_size)
    return weight_maps


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 70)
    log.info("generate_weights — start")
    log.info("=" * 70)
    overall = time.perf_counter()
    data_dir = _data_dir()

    masks = _build_schaefer_masks(data_dir)
    log.info("masks done — %d networks, expected (%d,) bool", len(masks), NUM_VERTICES)

    weights = _build_neurosynth_weights(data_dir)
    log.info("weights done — %d terms", len(weights))

    log.info("=" * 70)
    log.info("generate_weights — done in %.1fs", time.perf_counter() - overall)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
