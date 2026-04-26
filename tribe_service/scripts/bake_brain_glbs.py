"""Bake fsaverage5 cortical mesh GLBs with custom vertex attributes.

Output:
    aesthesis-app/public/brain/fsaverage5-{left,right}-{inflated,pial}.glb
    (4 files, ~700 KB each)

Each GLB carries the standard POSITION + NORMAL + indices, plus two
custom vertex attributes the frontend reads via drei's useGLTF:

    _PARCELID  uint16  per-vertex Schaefer-400 parcel index (0 = unassigned)
    _SULC      float32 per-vertex curvature, normalized to ~[-1, 1]

The underscore prefix is the glTF 2.0 convention for application-specific
attributes (ASSUMPTIONS_BRAIN.md §2.2). three.js's GLTFLoader (and drei's
useGLTF wrapper) preserves these as ``geometry.attributes._PARCELID`` etc.

This is a ONE-TIME bake. The output ships as Next.js static assets under
``aesthesis-app/public/brain/``. Browser fetches them on first results-page
load, then they're cached.

Usage:
    pip install nilearn nibabel pygltflib   # already in requirements-tribe.txt
    python -m tribe_service.scripts.bake_brain_glbs

    # or with custom output dir:
    BRAIN_OUTPUT_DIR=/tmp/brain python -m tribe_service.scripts.bake_brain_glbs

Idempotency: outputs already on disk are preserved unless TRIBE_FORCE_REBUILD=1.
"""

from __future__ import annotations

import logging
import os
import sys
import time
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
log = logging.getLogger("bake_brain_glbs")

#: Vertices per hemisphere (fsaverage5 standard).
N_VERTICES_PER_HEMI: int = 10242


def _force_rebuild() -> bool:
    return os.getenv("TRIBE_FORCE_REBUILD", "0").lower() in ("1", "true", "yes")


def _output_dir() -> Path:
    """Resolve the output directory.

    Default: ``aesthesis-app/public/brain/`` relative to repo root. The
    bake script computes the path from its own location so it works
    regardless of CWD.
    """
    env_override = os.getenv("BRAIN_OUTPUT_DIR")
    if env_override:
        p = Path(env_override).expanduser().resolve()
    else:
        # repo_root / aesthesis-app / public / brain
        repo_root = _HERE.parents[2]
        p = repo_root / "aesthesis-app" / "public" / "brain"
    p.mkdir(parents=True, exist_ok=True)
    log.info("output_dir resolved to %s", p)
    return p


def _data_dir() -> Path:
    """Where the parcel map lives (same as bake_parcel_map.py)."""
    p = Path(os.getenv("TRIBE_DATA_DIR", "./data")).expanduser().resolve()
    if not p.exists():
        raise RuntimeError(
            f"TRIBE_DATA_DIR={p} does not exist. Run "
            "`python -m tribe_service.scripts.bake_parcel_map` first."
        )
    return p


def _load_parcel_map(data_dir: Path) -> np.ndarray:
    """Load the parcel map produced by bake_parcel_map.py."""
    path = data_dir / "schaefer400_parcels.npy"
    if not path.exists():
        raise RuntimeError(
            f"Parcel map not found at {path}. Run "
            "`python -m tribe_service.scripts.bake_parcel_map` first."
        )
    arr = np.load(path)
    if arr.shape != (NUM_VERTICES,):
        raise RuntimeError(
            f"Parcel map at {path} has bad shape {arr.shape}; "
            f"expected ({NUM_VERTICES},)."
        )
    if arr.dtype != np.uint16:
        log.warning("parcel map dtype %s; casting to uint16", arr.dtype)
        arr = arr.astype(np.uint16)
    return arr


def _load_surface(gii_path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Load a GIFTI surface file, return (vertices, faces).

    Vertices: (N, 3) float32. Faces: (M, 3) int32.

    Uses nibabel directly because nilearn's surface loader returns a Mesh
    object that's awkward to unwrap. nibabel's GIFTI parser is the
    canonical reader.
    """
    import nibabel as nib  # type: ignore  # noqa: WPS433

    gii = nib.load(str(gii_path))
    coords = None
    triangles = None
    for darray in gii.darrays:
        intent = nib.nifti1.intent_codes.niistring[darray.intent]
        if intent == "NIFTI_INTENT_POINTSET":
            coords = darray.data.astype(np.float32)
        elif intent == "NIFTI_INTENT_TRIANGLE":
            triangles = darray.data.astype(np.int32)
    if coords is None:
        raise RuntimeError(f"no POINTSET in {gii_path}")
    if triangles is None:
        raise RuntimeError(f"no TRIANGLE in {gii_path}")
    return coords, triangles


def _load_sulc(gii_path: str | Path) -> np.ndarray:
    """Load a sulcal-curvature GIFTI map, return (N,) float32."""
    import nibabel as nib  # type: ignore  # noqa: WPS433

    gii = nib.load(str(gii_path))
    if not gii.darrays:
        raise RuntimeError(f"no darrays in sulc file {gii_path}")
    sulc = gii.darrays[0].data.astype(np.float32)
    return sulc


def _normalize_sulc(sulc: np.ndarray) -> np.ndarray:
    """Normalize sulcal curvature to roughly [-1, 1] via robust scaling.

    The raw sulc values from FreeSurfer are unbounded (typically -3 to 3,
    sometimes wider). For per-vertex shading we want a bounded value so
    the fragment math behaves predictably. Robust scaling (5th/95th
    percentile) avoids extreme outliers stealing the entire range.
    """
    lo = float(np.percentile(sulc, 5))
    hi = float(np.percentile(sulc, 95))
    if hi - lo < 1e-6:
        log.warning("sulc has near-zero range (%.3f .. %.3f)", lo, hi)
        return np.zeros_like(sulc)
    centered = (sulc - 0.5 * (lo + hi)) / (0.5 * (hi - lo))
    return np.clip(centered, -1.5, 1.5).astype(np.float32)


def _compute_vertex_normals(
    vertices: np.ndarray, faces: np.ndarray,
) -> np.ndarray:
    """Per-vertex normals via summed face-normal averaging.

    Faster than three.js's runtime computeVertexNormals because we only
    do it once at bake time. Pre-baking these saves the browser ~50ms
    per GLB load.
    """
    n = vertices.shape[0]
    normals = np.zeros((n, 3), dtype=np.float32)

    v0 = vertices[faces[:, 0]]
    v1 = vertices[faces[:, 1]]
    v2 = vertices[faces[:, 2]]
    face_normals = np.cross(v1 - v0, v2 - v0)
    # Don't normalize per-face yet — area-weighted summing produces
    # smoother per-vertex normals.

    np.add.at(normals, faces[:, 0], face_normals)
    np.add.at(normals, faces[:, 1], face_normals)
    np.add.at(normals, faces[:, 2], face_normals)

    # Normalize per-vertex.
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    safe = np.where(lens < 1e-9, 1.0, lens)
    return (normals / safe).astype(np.float32)


def _write_glb(
    out_path: Path,
    vertices: np.ndarray,
    faces: np.ndarray,
    normals: np.ndarray,
    parcel_ids: np.ndarray,
    sulc: np.ndarray,
) -> None:
    """Write a GLB with POSITION + NORMAL + indices + _PARCELID + _SULC.

    pygltflib does not have a one-shot helper for custom attributes, so
    we build the buffer / bufferView / accessor graph manually. The order
    matters: each accessor references a bufferView, each bufferView
    references the buffer. Byte offsets must be 4-byte aligned per the
    glTF 2.0 spec.

    ASSUMPTIONS_BRAIN.md §2.2-§2.4.
    """
    import pygltflib  # type: ignore  # noqa: WPS433

    # Coerce dtypes to spec-compliant types.
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    normals = np.ascontiguousarray(normals, dtype=np.float32)
    faces = np.ascontiguousarray(faces, dtype=np.uint32)
    parcel_ids = np.ascontiguousarray(parcel_ids, dtype=np.uint16)
    sulc = np.ascontiguousarray(sulc, dtype=np.float32)

    # Build the binary blob with 4-byte alignment between accessors.
    def _pad(buf: bytearray) -> None:
        while len(buf) % 4 != 0:
            buf.append(0)

    blob = bytearray()
    bv_meta: list[tuple[int, int, int | None]] = []  # (offset, len, target)

    # POSITION
    off = len(blob)
    blob.extend(vertices.tobytes())
    bv_meta.append((off, len(vertices.tobytes()), pygltflib.ARRAY_BUFFER))
    _pad(blob)

    # NORMAL
    off = len(blob)
    blob.extend(normals.tobytes())
    bv_meta.append((off, len(normals.tobytes()), pygltflib.ARRAY_BUFFER))
    _pad(blob)

    # INDICES
    off = len(blob)
    blob.extend(faces.tobytes())
    bv_meta.append((off, len(faces.tobytes()), pygltflib.ELEMENT_ARRAY_BUFFER))
    _pad(blob)

    # _PARCELID
    off = len(blob)
    blob.extend(parcel_ids.tobytes())
    bv_meta.append((off, len(parcel_ids.tobytes()), pygltflib.ARRAY_BUFFER))
    _pad(blob)

    # _SULC
    off = len(blob)
    blob.extend(sulc.tobytes())
    bv_meta.append((off, len(sulc.tobytes()), pygltflib.ARRAY_BUFFER))
    _pad(blob)

    n_vertices = vertices.shape[0]
    n_indices = faces.size
    pos_min = vertices.min(axis=0).tolist()
    pos_max = vertices.max(axis=0).tolist()

    gltf = pygltflib.GLTF2()
    gltf.buffers = [pygltflib.Buffer(byteLength=len(blob))]
    gltf.bufferViews = [
        pygltflib.BufferView(
            buffer=0, byteOffset=meta[0], byteLength=meta[1], target=meta[2],
        )
        for meta in bv_meta
    ]

    gltf.accessors = [
        # 0: POSITION
        pygltflib.Accessor(
            bufferView=0, componentType=pygltflib.FLOAT,
            count=n_vertices, type=pygltflib.VEC3,
            min=pos_min, max=pos_max,
        ),
        # 1: NORMAL
        pygltflib.Accessor(
            bufferView=1, componentType=pygltflib.FLOAT,
            count=n_vertices, type=pygltflib.VEC3,
        ),
        # 2: INDICES
        pygltflib.Accessor(
            bufferView=2, componentType=pygltflib.UNSIGNED_INT,
            count=n_indices, type=pygltflib.SCALAR,
        ),
        # 3: _PARCELID
        pygltflib.Accessor(
            bufferView=3, componentType=pygltflib.UNSIGNED_SHORT,
            count=n_vertices, type=pygltflib.SCALAR,
        ),
        # 4: _SULC
        pygltflib.Accessor(
            bufferView=4, componentType=pygltflib.FLOAT,
            count=n_vertices, type=pygltflib.SCALAR,
        ),
    ]

    primitive = pygltflib.Primitive(
        attributes=pygltflib.Attributes(POSITION=0, NORMAL=1),
        indices=2,
        mode=pygltflib.TRIANGLES,
    )
    # Custom attributes — underscore prefix per glTF 2.0 spec.
    # ASSUMPTIONS_BRAIN.md §2.2.
    primitive.attributes._PARCELID = 3  # type: ignore[attr-defined]
    primitive.attributes._SULC = 4      # type: ignore[attr-defined]

    mesh = pygltflib.Mesh(primitives=[primitive])
    gltf.meshes = [mesh]
    gltf.nodes = [pygltflib.Node(mesh=0)]
    gltf.scenes = [pygltflib.Scene(nodes=[0])]
    gltf.scene = 0

    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(str(out_path))

    size_kb = out_path.stat().st_size / 1024.0
    log.info("wrote %s (%.1f KB)", out_path, size_kb)


def bake_one(
    *,
    hemi: str,
    variant: str,
    fsavg: dict,
    parcel_map_full: np.ndarray,
    output_dir: Path,
) -> None:
    """Bake a single hemisphere/variant GLB."""
    if hemi not in ("left", "right"):
        raise ValueError(f"hemi must be 'left' or 'right'; got {hemi}")
    if variant not in ("inflated", "pial"):
        raise ValueError(f"variant must be 'inflated' or 'pial'; got {variant}")

    out_path = output_dir / f"fsaverage5-{hemi}-{variant}.glb"
    if out_path.exists() and not _force_rebuild():
        log.info("%s exists — skipping (set TRIBE_FORCE_REBUILD=1 to rebuild)",
                 out_path)
        return

    # Pick the correct nilearn key (handles 'infl' vs 'inflated' alias).
    surf_key = {
        ("left", "inflated"): "infl_left",
        ("right", "inflated"): "infl_right",
        ("left", "pial"): "pial_left",
        ("right", "pial"): "pial_right",
    }[(hemi, variant)]
    sulc_key = "sulc_left" if hemi == "left" else "sulc_right"

    log.info("baking %s/%s from %s", hemi, variant, fsavg[surf_key])
    t0 = time.perf_counter()

    vertices, faces = _load_surface(fsavg[surf_key])
    if vertices.shape[0] != N_VERTICES_PER_HEMI:
        raise RuntimeError(
            f"{hemi}/{variant} vertex count {vertices.shape[0]}; "
            f"expected {N_VERTICES_PER_HEMI}"
        )
    log.info("  vertices=%d, faces=%d", vertices.shape[0], faces.shape[0])

    sulc_raw = _load_sulc(fsavg[sulc_key])
    if sulc_raw.shape[0] != N_VERTICES_PER_HEMI:
        raise RuntimeError(
            f"sulc count {sulc_raw.shape[0]} != vertex count {N_VERTICES_PER_HEMI}"
        )
    sulc = _normalize_sulc(sulc_raw)

    # Slice the right hemisphere out of the concatenated parcel map.
    if hemi == "left":
        parcel_ids = parcel_map_full[:N_VERTICES_PER_HEMI]
    else:
        parcel_ids = parcel_map_full[N_VERTICES_PER_HEMI:]
    if parcel_ids.shape[0] != N_VERTICES_PER_HEMI:
        raise RuntimeError(
            f"parcel slice for {hemi} has shape {parcel_ids.shape}"
        )
    n_assigned = int(np.count_nonzero(parcel_ids))
    log.info("  parcel coverage: %d/%d (%.1f%%)",
             n_assigned, N_VERTICES_PER_HEMI,
             100.0 * n_assigned / N_VERTICES_PER_HEMI)

    normals = _compute_vertex_normals(vertices, faces)
    log.info("  vertex normals computed in %.2fs",
             time.perf_counter() - t0)

    _write_glb(out_path, vertices, faces, normals, parcel_ids, sulc)
    log.info("  total bake time: %.2fs", time.perf_counter() - t0)


def main() -> None:
    log.info("==== bake_brain_glbs start ====")
    output_dir = _output_dir()
    data_dir = _data_dir()
    parcel_map_full = _load_parcel_map(data_dir)

    from nilearn import datasets  # type: ignore  # noqa: WPS433

    log.info("fetching fsaverage5 mesh paths")
    t0 = time.perf_counter()
    fsavg = datasets.fetch_surf_fsaverage(mesh="fsaverage5")
    log.info("fsaverage5 paths fetched in %.1fs", time.perf_counter() - t0)

    for hemi in ("left", "right"):
        for variant in ("inflated", "pial"):
            bake_one(
                hemi=hemi,
                variant=variant,
                fsavg=fsavg,
                parcel_map_full=parcel_map_full,
                output_dir=output_dir,
            )
    log.info("==== bake_brain_glbs done ====")


if __name__ == "__main__":
    main()
