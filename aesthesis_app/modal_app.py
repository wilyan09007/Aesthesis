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
    )
    .env({"UPLOAD_DIR": "/tmp/uploads"})
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


if __name__ == "__main__":
    print("deploy: modal deploy modal_app.py")
    print("logs:   modal app logs aesthesis-orchestrator --tail")
