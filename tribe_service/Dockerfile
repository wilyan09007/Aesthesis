# Standalone container for running the TRIBE service outside Modal.
# Modal users should prefer modal_app.py instead — it's faster to iterate on.
#
# Build: docker build -t aesthesis-tribe .
# Run:   docker run --gpus all -p 8001:8001 -v $(pwd)/data:/app/data \
#          aesthesis-tribe

FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRIBE_DATA_DIR=/app/data \
    PIP_NO_CACHE_DIR=1

# Native deps (DESIGN.md §5.9)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        espeak-ng \
        redis-server \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps — keep this layer small enough to cache aggressively.
COPY ../requirements-tribe.txt /app/requirements-tribe.txt
RUN pip install -r /app/requirements-tribe.txt

# tribev2 from source — invalidates only when the install command changes.
RUN pip install git+https://github.com/facebookresearch/tribev2

# Service code last so iterating on it doesn't bust the heavy layers.
COPY tribe_neural /app/tribe_neural
COPY pyproject.toml /app/pyproject.toml

EXPOSE 8001
CMD ["uvicorn", "tribe_neural.api:app", "--host", "0.0.0.0", "--port", "8001"]
