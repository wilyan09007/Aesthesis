"""Modal deployment for the Aesthesis orchestrator.

One function — ``fastapi_app`` — wraps the existing FastAPI app at
``aesthesis.main:app`` and exposes it at the URL Modal returns from
``modal deploy``. Scales to zero by default (~3-5s cold start on the
first request after idle); the pipeline itself runs ~6-13s end to end
once warm.

Workflow on a fresh deploy::

    cd aesthesis_app
    modal secret create aesthesis-orchestrator   # see keys below
    modal deploy modal_app.py
    curl https://<your-org>--aesthesis-orchestrator.modal.run/health  # 200

Then point the frontend at the returned URL by setting
``NEXT_PUBLIC_AESTHESIS_API_URL`` in Vercel and redeploying.

Required secret keys (all sourced from the project ``.env``):
    TRIBE_SERVICE_URL          — URL of the Tribe Modal app
    GEMINI_API_KEY             — Google AI Studio key
    GEMINI_MODEL_INSIGHTS      — e.g. gemini-3.1-flash-lite-preview
    GEMINI_MODEL_VERDICT       — e.g. gemini-3.1-flash-lite-preview
    CORS_ALLOW_ORIGINS         — comma list incl. the Vercel frontend URL

Filesystem note: per-request uploads land in /tmp (ephemeral) and are
cleaned up after the response. No volume needed because nothing persists
across requests.
"""

from __future__ import annotations

import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "pydantic>=2.5",
        "httpx>=0.27",
        "numpy>=1.26",
        "python-multipart>=0.0.9",
        "ffmpeg-python>=0.2",
        "google-generativeai>=0.7",
        # PIL drives the bbox overlay on per-insight annotated
        # screenshots (aesthesis/annotate.py). Without it the overlay
        # silently falls back to "no annotated screenshot" — agent
        # prompts still render, but the user-facing image disappears.
        "Pillow>=10.0",
    )
    .env({
        "UPLOAD_DIR": "/tmp/uploads",
        # Vercel mints a fresh URL per preview/branch deploy
        # (aesthesis-frontend-<hash>-<team>.vercel.app); an exact-match CORS
        # allowlist would reject those and the browser would surface the
        # rejection as TypeError: Failed to fetch — looking to the user
        # like the backend went away. Regex covers canonical + every
        # preview URL for the project. CORS_ALLOW_ORIGINS in the named
        # secret stays authoritative for explicit prod hosts.
        # Note: ``[.]`` instead of ``\.`` because Modal's image-env serialises
        # the value through a layer that rejects unrecognised backslash escapes.
        # Same regex semantics, no escape headaches.
        "CORS_ALLOW_ORIGIN_REGEX": "^https://aesthesis-frontend(-[a-z0-9-]+)?[.]vercel[.]app$",
    })
    .add_local_python_source("aesthesis")
)

app = modal.App("aesthesis-orchestrator", image=image)


@app.function(
    cpu=2,
    memory=4 * 1024,
    timeout=600,
    min_containers=0,
    secrets=[modal.Secret.from_name("aesthesis-orchestrator")],
)
@modal.asgi_app(label="aesthesis-orchestrator")
def fastapi_app():
    """Serve at https://<org>--aesthesis-orchestrator.modal.run."""
    from aesthesis.main import app  # noqa: WPS433 — lazy import inside container
    return app


@app.function(
    cpu=2,
    memory=4 * 1024,
    timeout=600,
    min_containers=0,
    secrets=[modal.Secret.from_name("aesthesis-orchestrator")],
)
def analyze_blocking(video_bytes: bytes, goal: str | None, run_id: str) -> dict:
    """Background analyze job spawned via ``Function.spawn`` from the
    FastAPI ``/api/analyze`` handler.

    Why this function exists: Modal's web proxy enforces a ~150s sync
    timeout on web endpoints (``@modal.asgi_app``), regardless of the
    function's ``timeout=`` parameter. On real videos, V-JEPA encoding
    alone takes 80–130s, plus the orchestrator's screenshot fan-out
    plus two Gemini calls plus the upstream Tribe HTTP — total wall
    time routinely exceeds 150s. Modal's proxy then drops the
    browser → orchestrator connection mid-pipeline and the browser
    sees ``TypeError: Failed to fetch`` even though Tribe is still
    happily processing on the other side.

    This function is NOT exposed as a web endpoint. It's a regular
    Modal function invoked via internal RPC (``Function.spawn``), and
    the only timeout that applies is the function's own ``timeout=600s``
    ceiling. The browser hits the FastAPI handler (which returns a
    job_id in <1s), then polls a separate status endpoint that does a
    non-blocking ``FunctionCall.get(timeout=0)`` against this job. No
    long-held HTTP connection, no proxy ceiling.

    Returns the AnalyzeResponse as a plain dict (Pydantic ``model_dump``).
    Exceptions propagate to the caller via ``FunctionCall.get()`` — the
    /api/analyze/status endpoint catches and serializes them as
    ``{"status": "failed", "error": ...}``.
    """
    import asyncio  # noqa: WPS433
    import logging  # noqa: WPS433
    import shutil  # noqa: WPS433
    from pathlib import Path  # noqa: WPS433

    from aesthesis.config import get_config  # noqa: WPS433
    from aesthesis.logging_config import configure_logging  # noqa: WPS433
    from aesthesis.orchestrator import run_analysis  # noqa: WPS433

    configure_logging()
    log = logging.getLogger("aesthesis.modal.analyze_blocking")

    cfg = get_config()
    work_dir = cfg.upload_dir / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(video_bytes)
    log.info(
        "analyze_blocking begin run_id=%s size=%d",
        run_id, len(video_bytes),
        extra={"step": "modal.analyze_blocking", "run_id": run_id,
               "size_bytes": len(video_bytes)},
    )

    try:
        # run_analysis is async; we're in a sync Modal function context,
        # so spin up a fresh event loop. asyncio.run is fine here because
        # this function gets its own container per call.
        response = asyncio.run(
            run_analysis(cfg=cfg, video=video_path, goal=goal, run_id=run_id),
        )
        log.info(
            "analyze_blocking done run_id=%s elapsed_ms=%.1f n_insights=%d",
            run_id, response.elapsed_ms, len(response.insights),
            extra={"step": "modal.analyze_blocking", "run_id": run_id,
                   "elapsed_ms": response.elapsed_ms,
                   "n_insights": len(response.insights)},
        )
        return response.model_dump(mode="json")
    finally:
        # Clean up the upload regardless of success/failure. The
        # ephemeral filesystem will go away when the container exits
        # anyway, but explicit cleanup keeps long-warm containers tidy.
        if cfg.cleanup_uploads:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    print("deploy: modal deploy modal_app.py")
    print("logs:   modal app logs aesthesis-orchestrator --tail")
