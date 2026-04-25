"""Modal deployment stub for the TRIBE service.

Run `modal deploy modal_app.py` to ship. Modal will give you back a URL
that the Aesthesis app POSTs to:

    https://<org>--aesthesis-tribe-process-video-timeline.modal.run

`keep_warm=1` is set per DESIGN.md D2 — pin the GPU worker hot during the
demo window so requests don't pay the multi-minute model-load cold start.

Cost: A100-40GB on Modal is per-second billed. One warm container running
through a 30-min demo window costs roughly the price of a coffee.
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
        "nilearn>=0.10",
        "nibabel>=5.1",
        "nimare>=0.3",
        "nltools>=0.5",
        # audio (whisperx + edge-tts pulled by tribev2)
        "whisperx==3.1.1",
        "edge-tts>=6.1",
        "ffmpeg-python>=0.2",
        # GPU
        "torch>=2.1",
        "torchaudio>=2.1",
        "torchvision>=0.16",
    )
    # Install tribev2 from GitHub. Pulls V-JEPA 2, DINOv2, Wav2Vec-BERT,
    # LLaMA 3.2 weights from HuggingFace on first model load.
    .run_commands("pip install git+https://github.com/facebookresearch/tribev2")
    # Copy the service code into the image.
    .add_local_python_source("tribe_neural")
)

stub = modal.App("aesthesis-tribe", image=image)

# ─── Persistent volume for cached resources ──────────────────────────────────
#
# The Schaefer mask build, Neurosynth weight maps, and TRIBE model
# checkpoint are all cached to disk on first run (~30 min one-time, see
# DESIGN.md §5.8). Mounting a Modal volume keeps that work from being
# repeated on every cold start.
volume = modal.Volume.from_name(
    "aesthesis-tribe-data", create_if_missing=True
)


# ─── Web endpoint ────────────────────────────────────────────────────────────

@stub.function(
    gpu=modal.gpu.A100(memory=40),
    keep_warm=1,
    timeout=600,
    cpu=4,
    memory=32 * 1024,
    volumes={"/app/data": volume},
    secrets=[
        # Optional — only needed if HF rate-limits anonymous downloads.
        modal.Secret.from_name("huggingface-token", required_keys=[]),
    ],
)
@modal.asgi_app(label="aesthesis-tribe")
def fastapi_app():
    """Serve the FastAPI app on a Modal-managed URL."""
    import os
    os.environ.setdefault("TRIBE_DATA_DIR", "/app/data")
    from tribe_neural.api import app  # noqa: WPS433 — lazy import inside container
    return app


@stub.function(gpu=modal.gpu.A100(memory=40), volumes={"/app/data": volume}, timeout=3600)
def warmup() -> dict:
    """One-shot job to populate the data volume on a fresh deployment.

    Run via `modal run modal_app.py::warmup` after the first deploy. This
    runs the Schaefer mask build + Neurosynth meta-analysis + signature
    projection that DESIGN.md §5.8 describes (~35 min). Idempotent — safe
    to re-run; existing artifacts are kept.
    """
    import os
    import sys
    os.environ["TRIBE_DATA_DIR"] = "/app/data"
    from tribe_neural.init_resources import load_resources

    res = load_resources(force_mock=False)
    return {
        "ok": True,
        "n_masks": len(res.masks),
        "n_weight_maps": len(res.weight_maps),
        "vifs_loaded": res.vifs is not None,
        "data_dir": str(res.data_dir),
        "python": sys.version,
    }


if __name__ == "__main__":
    print("Run with: modal deploy modal_app.py")
