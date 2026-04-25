"""Project pretrained CANLab pain/affect signatures to fsaverage5.

Both signatures are *optional* per ``init_resources._try_load_signature``,
which returns ``None`` on missing. So this script is best-effort: a failed
download (network blocked, repo moved, signature deprecated) only logs a
warning. The TRIBE service still boots without these — they're only consumed
by the ``trust_affinity`` ROI's VIFS-subtraction term (DESIGN.md §5.16.2 row
4) and the optional PINES negative-emotion overlay.

Outputs (under ``$TRIBE_DATA_DIR``, default ``./data``):

    vifs_surface.npy   — float64 (NUM_VERTICES,)   — Krishnan 2020 VIFS pattern
    pines_surface.npy  — float64 (NUM_VERTICES,)   — Chang 2015 PINES pattern

Source: https://github.com/canlab/Neuroimaging_Pattern_Masks
"""

from __future__ import annotations

import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
from tribe_neural.constants import NUM_VERTICES  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)5s] %(name)s :: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("project_signatures")


SIGNATURE_URLS: dict[str, str] = {
    "vifs": (
        "https://github.com/canlab/Neuroimaging_Pattern_Masks/raw/master/"
        "Multivariate_signature_patterns/2020_Krishnan_eLife_VIFS/"
        "bmrk5_VIFS_unthresholded.nii.gz"
    ),
    "pines": (
        "https://github.com/canlab/Neuroimaging_Pattern_Masks/raw/master/"
        "Multivariate_signature_patterns/2015_Chang_PLS_Neuroimage_PINES/"
        "Rating_Weights_LOSO_2.nii.gz"
    ),
}


def _data_dir() -> Path:
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    log.info("data_dir resolved to %s", p)
    return p


def _force_rebuild() -> bool:
    return os.getenv("TRIBE_FORCE_REBUILD", "0").lower() in ("1", "true", "yes")


def _download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        log.info("  cached at %s (%d bytes), skipping download",
                 dest, dest.stat().st_size)
        return True
    log.info("  GET %s", url)
    log.info("  → %s", dest)
    t0 = time.perf_counter()
    try:
        # Set a UA so GitHub doesn't 403 us as a default Python client.
        req = urllib.request.Request(url, headers={"User-Agent": "tribe-warmup/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
            chunk_size = 1 << 20
            total = 0
            while True:
                buf = resp.read(chunk_size)
                if not buf:
                    break
                out.write(buf)
                total += len(buf)
            log.info("  ok — %d bytes in %.1fs", total, time.perf_counter() - t0)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log.warning("  download failed: %s", e)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return False


def _project_to_fsaverage5(nifti_path: Path, fsavg) -> np.ndarray:
    from nilearn import surface  # type: ignore

    log.info("  projecting %s → fsaverage5 (linear)", nifti_path)
    t = time.perf_counter()
    lh = surface.vol_to_surf(
        str(nifti_path), fsavg["pial_left"], interpolation="linear",
    )
    rh = surface.vol_to_surf(
        str(nifti_path), fsavg["pial_right"], interpolation="linear",
    )
    surf = np.concatenate([lh, rh]).astype(np.float64)
    surf = np.nan_to_num(surf, nan=0.0, posinf=0.0, neginf=0.0)
    log.info(
        "  surface: shape=%s min=%.3f max=%.3f mean=%.3f in %.1fs",
        surf.shape, float(surf.min()), float(surf.max()), float(surf.mean()),
        time.perf_counter() - t,
    )
    if surf.shape != (NUM_VERTICES,):
        raise RuntimeError(
            f"unexpected surface shape {surf.shape}, expected ({NUM_VERTICES},)"
        )
    return surf


def main() -> None:
    from nilearn import datasets  # type: ignore

    log.info("=" * 70)
    log.info("project_signatures — start")
    log.info("=" * 70)
    data_dir = _data_dir()
    sig_dir = data_dir / "signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)

    log.info("fetching fsaverage5 mesh")
    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")

    summary: list[tuple[str, str]] = []
    for name, url in SIGNATURE_URLS.items():
        log.info("─── %s ───", name)
        out_npy = data_dir / f"{name}_surface.npy"
        if out_npy.exists() and not _force_rebuild():
            log.info("  surface npy already at %s — skipping", out_npy)
            summary.append((name, "skip-cached"))
            continue

        nii_path = sig_dir / f"{name}.nii.gz"
        if not _download(url, nii_path):
            log.warning("  signature %s skipped (download failed)", name)
            summary.append((name, "skip-download-failed"))
            continue

        try:
            surf = _project_to_fsaverage5(nii_path, fsavg)
        except Exception as e:  # noqa: BLE001
            log.error("  projection failed: %s — skipping", e)
            summary.append((name, f"skip-projection-failed:{e}"))
            continue

        np.save(out_npy, surf)
        log.info("  saved %s (%d bytes)", out_npy, out_npy.stat().st_size)
        summary.append((name, "ok"))

    log.info("=" * 70)
    for name, status in summary:
        log.info("  %s: %s", name, status)
    log.info("project_signatures — done")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
