"""Modal deployment for the TRIBE service.

Two functions:

1. ``fastapi_app`` — the user-facing GPU worker. Mounted at the URL
   Modal hands back from ``modal deploy``. Scales to zero by default
   (``min_containers=0``); the first request after idle pays a model-load
   cold start. Set ``min_containers=1`` for warm-on-demand-day demos.

2. ``populate_data`` — one-shot CPU job that builds the Schaefer masks,
   Neurosynth term-association weight maps, and (optionally) the CANLab
   pain/affect signatures into the persistent volume. **Must be run once
   on a fresh volume**, otherwise the FastAPI app's startup hook crashes
   (``init_resources._load_masks`` raises if ``/app/data/masks`` is empty).

Workflow on a brand-new deploy::

    modal deploy modal_app.py
    modal run modal_app.py::populate_data        # ~30-60 min, one-time
    curl https://<your-org>--aesthesis-tribe.modal.run/health   # 200

The volume ``aesthesis-tribe-data`` survives across deploys, so step 2
only runs once unless ``TRIBE_FORCE_REBUILD=1`` is in the environment
or the volume is wiped.

Cost notes (Modal billing, US-east A100-40GB):
- ``fastapi_app`` at ``min_containers=0`` is $0 idle, ~$1.30/hr while in use.
- ``populate_data`` runs CPU-only (no GPU) and finishes well under $1.

DESIGN.md §5.8 (resource generation), §5.15.6 (Phase 0 spike).
"""

from __future__ import annotations

import modal  # type: ignore  # noqa: I001

# ─── Image ───────────────────────────────────────────────────────────────────

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "espeak-ng", "redis-server")
    .pip_install(
        # service plumbing
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "pydantic>=2.5",
        "arq>=0.26",
        "redis>=5.0",
        "python-multipart>=0.0.9",
        # numerics + neuro
        "numpy>=1.26",
        "scipy>=1.11",
        "pandas>=2.0",
        # nilearn pinned to the range nimare accepts (nimare requires
        # >=0.12, <0.14 across all recent releases).
        "nilearn>=0.12,<0.14",
        "nibabel>=5.1",
        # nimare pinned exact so pip doesn't backtrack to 0.5.x (which
        # wants nilearn<0.12 and breaks our pin). nltools is intentionally
        # not listed: latest nltools (0.5.1) still requires numpy<1.24,
        # incompatible with torch>=2.1's numpy>=1.26. If tribev2 truly
        # needs it, the GitHub install below will surface that as a
        # transitive-dep error with a clearer message.
        "nimare==0.16.0",
        # audio (whisperx + edge-tts pulled by tribev2)
        "whisperx==3.1.1",
        "edge-tts>=6.1",
        "ffmpeg-python>=0.2",
        # decord — fast video decoder used by the batched-encoding patch
        # in tribe_runner.py to pre-decode the entire MP4 once instead of
        # making 1,728 random-access moviepy seeks per request. C++ under
        # the hood, ~50-100x faster than moviepy on whole-video reads.
        # Patch falls back to moviepy if this import fails.
        "decord>=0.6.0",
        # GPU
        "torch>=2.1",
        "torchaudio>=2.1",
        "torchvision>=0.16",
    )
    # Install tribev2 from GitHub. The forward pass references V-JEPA 2,
    # DINOv2, Wav2Vec-BERT, and LLaMA 3.2 from HuggingFace, but with
    # audio physically stripped at the request boundary
    # (api._strip_audio_track), tribev2.main prunes the audio and text
    # extractors before any weight fetch, so only the video path
    # actually pulls weights at runtime — V-JEPA-2-vitg-fpc64-256.
    .run_commands("pip install git+https://github.com/facebookresearch/tribev2")
    # spaCy's en_core_web_lg is needed by tribev2.eventstransforms because
    # other transformations import spaCy at module load time. Bake it into
    # the image so the first request doesn't pay the download cost. NOTE:
    # whisperx is in pip_install above; with audio stripped at the request
    # boundary, tribev2 prunes the text extractor before whisperx is ever
    # invoked, so the binary is present but never executed.
    .run_commands("python -m spacy download en_core_web_lg")
    # Pre-fetch V-JEPA-2-vitg-fpc64-256 (~5 GB) into the image's HF cache
    # at /root/.cache/huggingface. Otherwise every cold container pays a
    # ~35 s download on the first request — neuralset.extractors.video
    # loads V-JEPA lazily on first inference, not at TribeModel
    # construction time, so it always misses the volume cache (which
    # holds the TRIBE checkpoint via TRIBE_DATA_DIR/cache, not the HF
    # cache). The HF token secret is forwarded so the build succeeds
    # against gated repos; V-JEPA-2-vitg is open-weight today, so this
    # is defence-in-depth, not a hard requirement. Audio/text encoders
    # (Wav2Vec-BERT, LLaMA-3.2, DINOv2) are deliberately NOT baked —
    # the audio-stripped MP4 causes tribev2.main to prune the audio and
    # text extractors before they're ever instantiated, so those weights
    # are never loaded; pulling them would balloon the image by ~20 GB.
    .run_commands(
        "python -c \"from huggingface_hub import snapshot_download; "
        "snapshot_download('facebook/vjepa2-vitg-fpc64-256')\"",
        secrets=[modal.Secret.from_name("huggingface-token", required_keys=[])],
    )
    # Service code + warmup scripts.
    .add_local_python_source("tribe_neural")
    .add_local_python_source("scripts")
)

app = modal.App("aesthesis-tribe", image=image)

# ─── Persistent volume ───────────────────────────────────────────────────────
#
# Survives across deploys. Holds the Schaefer masks, Neurosynth weight maps,
# CANLab signatures, and (eventually) the cached TRIBE checkpoint on first
# model load. Wiping this volume forces ``populate_data`` to rebuild from
# scratch (~30-60 min).
volume = modal.Volume.from_name(
    "aesthesis-tribe-data", create_if_missing=True
)


# ─── Web endpoint ────────────────────────────────────────────────────────────

@app.function(
    gpu="A100-40GB",
    min_containers=0,
    timeout=600,
    cpu=4,
    memory=32 * 1024,
    volumes={"/app/data": volume},
    secrets=[
        # Required for downloading gated HF weights (LLaMA 3.2). The secret
        # exists with key=HF_TOKEN. Empty token is permitted (required_keys=[])
        # but model downloads will 403 against gated repos.
        modal.Secret.from_name("huggingface-token", required_keys=[]),
    ],
)
@modal.asgi_app(label="aesthesis-tribe")
def fastapi_app():
    """Serve the FastAPI app at https://<org>--aesthesis-tribe.modal.run."""
    import os
    os.environ.setdefault("TRIBE_DATA_DIR", "/app/data")
    # PyTorch CUDA memory allocator: enable expandable_segments so V-JEPA
    # can reuse memory across batched forwards without fragmentation
    # OOMs. At B=20 on A100-40GB without this flag, ~8 GB stays
    # "reserved but unallocated" and the next forward fails with
    # "CUDA out of memory ... Tried to allocate 3.75 GiB ... 306 MiB free".
    # Setting this BEFORE torch is imported is essential — PyTorch reads
    # the env var at first CUDA init and ignores it afterwards.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    from tribe_neural.api import app  # noqa: WPS433 — lazy import inside container
    return app


# ─── Volume populator (one-shot, CPU-only) ───────────────────────────────────

@app.function(
    cpu=8,
    memory=32 * 1024,
    timeout=3 * 3600,  # 3h ceiling: ~7×10 min MKDA + dataset fetch + buffer
    volumes={"/app/data": volume},
)
def populate_data(force_rebuild: bool = False) -> dict:
    """One-shot data-volume builder.

    Runs ``scripts.generate_weights`` (Schaefer masks + Neurosynth term
    maps) then ``scripts.project_signatures`` (CANLab VIFS / PINES, best
    effort). Both write under ``/app/data`` which is the mounted volume.

    Idempotent — the scripts skip artifacts that already exist unless
    ``force_rebuild=True`` is passed (or ``TRIBE_FORCE_REBUILD=1`` is in
    the env).

    Args:
        force_rebuild: If True, regenerate masks + Neurosynth weights even
            if cached files exist on the volume. Useful after a bug fix
            in the resource-generation logic. The downloaded NiMARE corpus
            is preserved either way (NiMARE has its own ``overwrite``).

    Run via::

        modal run modal_app.py::populate_data
        modal run modal_app.py::populate_data --force-rebuild

    Watch progress with::

        modal app logs aesthesis-tribe --tail
    """
    import logging
    import os
    import sys
    import time

    os.environ["TRIBE_DATA_DIR"] = "/app/data"
    os.environ.setdefault("LOG_LEVEL", "INFO")
    if force_rebuild:
        os.environ["TRIBE_FORCE_REBUILD"] = "1"

    # Force-configure the root logger so script logs land in Modal's stdout
    # capture even if some library called basicConfig before us.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)5s] %(name)s :: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    log = logging.getLogger("populate_data")

    log.info("=" * 70)
    log.info("populate_data — start")
    log.info("python=%s", sys.version.split()[0])
    log.info("cwd=%s, data_dir=/app/data", os.getcwd())
    log.info("env TRIBE_FORCE_REBUILD=%r", os.getenv("TRIBE_FORCE_REBUILD"))
    log.info("=" * 70)
    overall_t0 = time.perf_counter()

    # ── Step 1: Schaefer masks + Neurosynth weights ──
    log.info("########## STEP 1/2: generate_weights ##########")
    t = time.perf_counter()
    from scripts import generate_weights  # type: ignore  # noqa: WPS433
    generate_weights.main()
    log.info(
        "########## generate_weights done in %.1fs ##########",
        time.perf_counter() - t,
    )

    # Commit so step-2 failure doesn't lose step-1 progress.
    try:
        volume.commit()
        log.info("volume committed after step 1")
    except Exception as e:  # noqa: BLE001
        log.warning("volume.commit failed (continuing): %s", e)

    # ── Step 2: optional CANLab signatures ──
    log.info("########## STEP 2/2: project_signatures ##########")
    t = time.perf_counter()
    try:
        from scripts import project_signatures  # type: ignore  # noqa: WPS433
        project_signatures.main()
        log.info(
            "########## project_signatures done in %.1fs ##########",
            time.perf_counter() - t,
        )
    except Exception as e:  # noqa: BLE001
        # Optional: don't fail the whole run if signatures can't be fetched.
        log.warning("project_signatures raised (continuing): %s", e, exc_info=True)

    try:
        volume.commit()
        log.info("volume committed after step 2")
    except Exception as e:  # noqa: BLE001
        log.warning("volume.commit failed (continuing): %s", e)

    # ── Verify what landed on disk ──
    log.info("########## VERIFY ##########")
    masks_dir = "/app/data/masks"
    weights_path = "/app/data/neurosynth_weights.npz"
    vifs_path = "/app/data/vifs_surface.npy"
    pines_path = "/app/data/pines_surface.npy"

    n_masks = (
        len([f for f in os.listdir(masks_dir) if f.endswith(".npy")])
        if os.path.isdir(masks_dir) else 0
    )
    weights_size = (
        os.path.getsize(weights_path) if os.path.exists(weights_path) else 0
    )

    summary = {
        "ok": n_masks == 7 and weights_size > 0,
        "elapsed_s": round(time.perf_counter() - overall_t0, 1),
        "n_mask_files": n_masks,
        "weights_npz_bytes": weights_size,
        "vifs_present": os.path.exists(vifs_path),
        "pines_present": os.path.exists(pines_path),
        "data_dir": "/app/data",
    }
    log.info("=" * 70)
    log.info("populate_data — summary: %s", summary)
    log.info("=" * 70)
    return summary


# ─── Quick verifier (optional) ───────────────────────────────────────────────

@app.function(
    cpu=2, memory=8 * 1024, timeout=600,
    volumes={"/app/data": volume},
)
def verify_resources() -> dict:
    """Read the volume and exercise ``init_resources`` without any GPU.

    Useful as a smoke test after ``populate_data``: confirms the FastAPI
    lifespan startup will succeed before the user sends real requests.

        modal run modal_app.py::verify_resources
    """
    import logging
    import os
    import sys

    os.environ["TRIBE_DATA_DIR"] = "/app/data"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)5s] %(name)s :: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    log = logging.getLogger("verify_resources")

    # Avoid TribeRunner — it imports torch and tries to instantiate the
    # GPU pipeline. We only need to verify the cached arrays load.
    from tribe_neural import init_resources  # type: ignore
    masks = init_resources._load_masks(init_resources._data_dir())  # type: ignore
    weights = init_resources._load_weight_maps(init_resources._data_dir())  # type: ignore
    vifs = init_resources._try_load_signature(init_resources._data_dir(), "vifs")  # type: ignore
    pines = init_resources._try_load_signature(init_resources._data_dir(), "pines")  # type: ignore

    summary = {
        "ok": True,
        "n_masks": len(masks),
        "mask_shapes": {k: list(v.shape) for k, v in masks.items()},
        "n_weight_maps": len(weights),
        "weight_keys": sorted(weights.keys()),
        "vifs_loaded": vifs is not None,
        "pines_loaded": pines is not None,
    }
    log.info("verify_resources — %s", summary)
    return summary


if __name__ == "__main__":
    print("deploy:    modal deploy modal_app.py")
    print("populate:  modal run modal_app.py::populate_data")
    print("verify:    modal run modal_app.py::verify_resources")
