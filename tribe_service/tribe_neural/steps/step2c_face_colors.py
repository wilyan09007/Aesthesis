"""Per-face uint8 RGB color stream — matches Meta's TRIBE v2 demo format exactly.

Meta's demo uses pre-baked per-face colors served as uint8 RGB binary
(ASSUMPTIONS_BRAIN.md, reverse-engineered from the production bundle):

    shape = (n_TRs, n_faces=20480, 3=RGB)
    dtype = uint8
    order = "C" (face index moves fastest within a frame)

The custom WebGL shader on the frontend reads this directly into a
DataTexture and samples per-face per-frame. No CPU work in the browser.

We emit ONE binary per hemisphere (left = first 10242 vertices of the
fsaverage5 layout, right = next 10242), encoded base64 in the JSON
response so it rides the existing wire without an extra fetch.

Wire size: 30 s × 20 TRs × 20480 faces × 3 bytes × 2 hemis ≈ 2.4 MB
binary, 3.3 MB after base64. Acceptable; matches Meta's order of
magnitude (~2.9 MB zip per clip).

Why per-face and not per-vertex:
- Meta does it this way (visible in face-colors.zip).
- Sharper boundaries between cortical regions (no smoothing over
  shared vertices).
- The shader is simpler — one sample per fragment.

Algorithm per face:
  face_z = mean(z[v0], z[v1], z[v2])     # avg of triangle's 3 vertex z-scores
  rgb     = colormap(face_z)              # diverging RdBu, anchored at 0
  out     = uint8(255 * clamp01(rgb))
"""

from __future__ import annotations

import base64
import logging
import struct
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# fsaverage5 split point. ASSUMPTIONS_BRAIN.md §1.1.
N_VERTICES_PER_HEMI: int = 10242
N_FACES_PER_HEMI: int = 20480


def _load_face_indices(glb_path: Path) -> np.ndarray:
    """Read the triangle index buffer out of a GLB. Returns shape
    ``(n_faces, 3)`` of uint32 vertex indices.

    Meta's GLB topology is byte-identical to ours (verified — both ship
    the canonical fsaverage5 face order). We can read either; we read
    Meta's because that's what the frontend renders.
    """
    import json
    data = glb_path.read_bytes()
    json_len, _ = struct.unpack_from("<I4s", data, 12)
    j = json.loads(data[20:20 + json_len])
    bin_off = 20 + json_len
    bin_data = data[bin_off + 8:]

    prim = j["meshes"][0]["primitives"][0]
    idx_acc = j["accessors"][prim["indices"]]
    bv = j["bufferViews"][idx_acc["bufferView"]]
    off = bv.get("byteOffset", 0) + idx_acc.get("byteOffset", 0)
    cnt = idx_acc["count"]
    dtype = np.uint32 if idx_acc["componentType"] == 5125 else np.uint16
    flat = np.frombuffer(bin_data[off:off + cnt * np.dtype(dtype).itemsize], dtype=dtype)
    return flat.reshape(-1, 3).astype(np.uint32, copy=False)


def _load_face_indices_cached() -> tuple[np.ndarray, np.ndarray]:
    """Load LH + RH face indices once per worker. The GLBs ship in
    ``aesthesis-app/public/brain/`` on the dev box, but the TRIBE
    worker doesn't have access to that. We bake the face arrays into
    the data volume during ``populate_data`` instead.

    For now: if the cached arrays are missing, generate them from the
    canonical fsaverage5 layout (which is what Meta also uses, byte
    identical).
    """
    import os
    data_dir = Path(os.getenv("TRIBE_DATA_DIR", "./data")).resolve()

    cache_lh = data_dir / "fsaverage5_face_indices_lh.npy"
    cache_rh = data_dir / "fsaverage5_face_indices_rh.npy"

    if cache_lh.exists() and cache_rh.exists():
        log.info("loaded cached face indices (lh + rh)")
        return np.load(cache_lh), np.load(cache_rh)

    # Build from nilearn (one-shot). Fail loudly if nilearn isn't
    # installed — we can't proceed without it.
    log.info("baking fsaverage5 face indices (one-time)")
    from nilearn import datasets  # type: ignore  # noqa: WPS433
    import nibabel as nib  # type: ignore  # noqa: WPS433

    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")

    def _faces(gii_path: str) -> np.ndarray:
        gii = nib.load(gii_path)
        for darray in gii.darrays:
            intent = nib.nifti1.intent_codes.niistring[darray.intent]
            if intent == "NIFTI_INTENT_TRIANGLE":
                return darray.data.astype(np.uint32)
        raise RuntimeError(f"no TRIANGLE darray in {gii_path}")

    lh_faces = _faces(fsavg["pial_left"])
    rh_faces = _faces(fsavg["pial_right"])

    if lh_faces.shape != (N_FACES_PER_HEMI, 3):
        raise RuntimeError(
            f"unexpected LH face shape {lh_faces.shape}; expected ({N_FACES_PER_HEMI}, 3)"
        )
    if rh_faces.shape != (N_FACES_PER_HEMI, 3):
        raise RuntimeError(
            f"unexpected RH face shape {rh_faces.shape}; expected ({N_FACES_PER_HEMI}, 3)"
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    np.save(cache_lh, lh_faces)
    np.save(cache_rh, rh_faces)
    log.info("cached face indices to %s", data_dir)
    return lh_faces, rh_faces


# ─── Glass-brain sparse-overlay colormap (transparent + high sensitivity) ──
#
# Two design goals (per user direction, ASSUMPTIONS_BRAIN.md §10):
#
#   (A) HIGH SENSITIVITY — even small activations (|z| ≥ 0.2) produce
#       visible color, ramping fast to saturation by |z| ≈ 1.5. Most of
#       the cortex shows colored signal at any instant, so the user
#       can read the spatial pattern directly.
#
#   (B) NEAR-TRANSPARENT SHELL — the resting cortex renders as a faint
#       ghost outline (alpha ≈ 0.10), so anatomy is just suggested
#       rather than dominant. Activated regions glow with high alpha
#       (up to ≈ 0.92), which combined with the gray→red/blue color
#       ramp produces a clean "glass brain with glowing patches" look.
#
# Output is RGBA (4 channels), unlike the prior RGB-only encoding.
# The alpha channel is consumed by the WebGL shader (which writes it
# into ``diffuseColor.a``) and the material is set to ``transparent =
# true, depthWrite = false`` so per-fragment alpha controls visibility.
#
# Wire-format change: this changes ``shape`` from (n_TRs, 20480, 3) to
# (n_TRs, 20480, 4) and ``byteStride`` from 3 to 4. Total size grows
# from ~737 KB/hemi to ~983 KB/hemi (still well under Meta's 1.5 MB).

# Resting-state base. Slightly cool gray so it reads as "ghost cortex"
# rather than "blank slab" — keeps directional shadowing visible in the
# faint outline.
_GHOST_BASE = np.array([0.50, 0.50, 0.58], dtype=np.float32)

# Saturated activation colors. More chromatic than before since the
# transparent shell needs strong color for active regions to read.
_COLD = np.array([0.18, 0.42, 0.96], dtype=np.float32)   # bright blue
_WARM = np.array([0.96, 0.20, 0.18], dtype=np.float32)   # bright red

# Sensitivity tuning. _Z_THRESH is the "barely-noticeable" floor. Below
# this the alpha stays at _BASE_ALPHA (faint shell). Above _Z_MAX the
# alpha and color both saturate.
_Z_THRESH = 0.2
_Z_MAX = 1.5

# Alpha curve — combined with the material's transparent flag, this
# drives the "glass brain glowing patches" effect.
_BASE_ALPHA = 0.10  # resting shell is ~90% transparent
_MAX_ALPHA = 0.92   # peak activations are ~92% opaque (still slight bleed)


def _diverging_color_batch(z: np.ndarray) -> np.ndarray:
    """Vectorized z-score → RGBA, glass-brain transparent-overlay style.

    Behaviour:
      |z| ≤ THRESH  → ghost gray, alpha = BASE_ALPHA (faint shell)
      THRESH < |z| < Z_MAX → linear interpolation to saturated color +
                              high alpha
      |z| ≥ Z_MAX   → saturated warm/cool, alpha = MAX_ALPHA

    Sign of z chooses warm (positive) vs cool (negative). Returns
    shape ``(..., 4)`` float32 in [0, 1].
    """
    z = np.where(np.isfinite(z), z, 0.0).astype(np.float32, copy=False)
    abs_z = np.abs(z)

    # Color blend: ghost gray → warm/cool, linear with |z|/Z_MAX.
    color_t = np.clip(abs_z / _Z_MAX, 0.0, 1.0)[..., None]
    pos_mask = (z >= 0)[..., None]
    target = np.where(pos_mask, _WARM, _COLD)
    rgb = _GHOST_BASE + (target - _GHOST_BASE) * color_t

    # Alpha ramp: BASE_ALPHA below threshold, linear to MAX_ALPHA above.
    span = max(1e-6, _Z_MAX - _Z_THRESH)
    a_ramp = np.clip((abs_z - _Z_THRESH) / span, 0.0, 1.0)
    alpha = _BASE_ALPHA + a_ramp * (_MAX_ALPHA - _BASE_ALPHA)
    # Lock to BASE_ALPHA below threshold (no slow ramp from 0).
    alpha = np.where(abs_z < _Z_THRESH, _BASE_ALPHA, alpha)

    return np.concatenate(
        [rgb, alpha[..., None]], axis=-1,
    ).astype(np.float32, copy=False)


# ─── Main ────────────────────────────────────────────────────────────────────

def extract_face_colors(preds: np.ndarray) -> dict:
    """Bake per-face per-frame uint8 RGB color streams (Meta-style).

    Args:
        preds: shape (n_TRs, 20484). TRIBE per-vertex predictions on
            fsaverage5 (left = first 10242, right = next 10242).

    Returns:
        Dict with keys ``left`` and ``right``, each a dict::

            {
                "format": "uint8_rgb_bin",
                "shape": [n_TRs, n_faces, 3],
                "n_frames": n_TRs,
                "n_faces": 20480,
                "data_b64": "<base64 of (n_TRs * 20480 * 3) uint8 bytes>",
            }

        The frontend decodes ``data_b64``, builds a DataTexture atlas,
        and samples per-face per-frame in the fragment shader. Format
        and layout match Meta's reverse-engineered demo spec exactly.

    Raises:
        ValueError: on shape mismatch or NaN/Inf inputs. Loud failure —
            we never want to silently bake garbage colors.
    """
    if preds.ndim != 2:
        raise ValueError(
            f"preds must be 2-D (n_TRs, n_vertices); got {preds.shape}"
        )
    n_trs, n_vertices = preds.shape
    if n_vertices != 2 * N_VERTICES_PER_HEMI:
        raise ValueError(
            f"preds vertex count {n_vertices}; expected {2 * N_VERTICES_PER_HEMI} "
            "(fsaverage5 lh+rh)"
        )
    if not np.isfinite(preds).all():
        n_bad = int((~np.isfinite(preds)).sum())
        raise ValueError(
            f"preds contains {n_bad} non-finite values (NaN/Inf). "
            "Refusing to bake face colors."
        )

    log.info(
        "extract_face_colors begin",
        extra={
            "step": "face_colors",
            "shape": list(preds.shape),
            "n_trs": int(n_trs),
            "n_faces_per_hemi": N_FACES_PER_HEMI,
        },
    )

    lh_faces, rh_faces = _load_face_indices_cached()

    # Z-score predictions per vertex across time (so neutral z=0 maps
    # to neutral gray and saturated activations stand out).
    mean = preds.mean(axis=0, keepdims=True)
    std = preds.std(axis=0, keepdims=True)
    safe_std = np.where(std < 1e-9, 1.0, std)
    z = (preds - mean) / safe_std
    z = np.where(std < 1e-9, 0.0, z).astype(np.float32, copy=False)

    out: dict = {}
    for hemi, faces, vertex_offset in (
        ("left", lh_faces, 0),
        ("right", rh_faces, N_VERTICES_PER_HEMI),
    ):
        # Slice this hemisphere's z-scores: (n_trs, n_vertices_per_hemi)
        z_hemi = z[:, vertex_offset:vertex_offset + N_VERTICES_PER_HEMI]
        # Per-face mean: average each triangle's 3 vertex z-scores.
        # Result shape: (n_trs, n_faces_per_hemi).
        # Indexing trick: z_hemi[:, faces] has shape (n_trs, n_faces, 3),
        # then mean axis=2 reduces to (n_trs, n_faces).
        z_face = z_hemi[:, faces].mean(axis=2)
        # Run through colormap → (n_trs, n_faces, 4) RGBA in [0, 1].
        rgba = _diverging_color_batch(z_face)
        # Quantize to uint8 RGBA
        rgba_u8 = np.clip(rgba * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if rgba_u8.shape != (n_trs, N_FACES_PER_HEMI, 4):
            raise RuntimeError(
                f"{hemi}: face color shape {rgba_u8.shape}; "
                f"expected ({n_trs}, {N_FACES_PER_HEMI}, 4)"
            )

        # Memory layout: face index moves fastest within a frame
        # (matches Meta's order convention but with RGBA per face
        # instead of RGB — alpha is the new bit). numpy default
        # tobytes() on (n_trs, n_faces, 4) C-contiguous gives
        # frame-major, face-minor, RGBA-final.
        binary = rgba_u8.tobytes()
        b64 = base64.b64encode(binary).decode("ascii")

        # Sanity stats useful for debugging the visualization on the
        # frontend ("why is the brain too transparent" etc.).
        a_chan = rgba_u8[..., 3]
        active = float((a_chan > 100).mean()) * 100.0
        very_active = float((a_chan > 200).mean()) * 100.0

        out[hemi] = {
            "format": "uint8_rgba_bin",
            "shape": [int(n_trs), int(N_FACES_PER_HEMI), 4],
            "n_frames": int(n_trs),
            "n_faces": int(N_FACES_PER_HEMI),
            "data_b64": b64,
        }
        log.info(
            "%s hemisphere face colors baked",
            hemi,
            extra={
                "step": "face_colors",
                "hemi": hemi,
                "shape": list(rgba_u8.shape),
                "binary_kb": round(len(binary) / 1024, 1),
                "b64_kb": round(len(b64) / 1024, 1),
                "pct_alpha_gt100": round(active, 1),
                "pct_alpha_gt200": round(very_active, 1),
            },
        )

    return out
