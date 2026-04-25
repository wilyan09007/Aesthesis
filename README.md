# Aesthesis — backend

Brain-judged A/B comparison via TRIBE v2. This repo hosts the **backend Step 2 (Assess) pipeline**: take two MP4 screen recordings, return a side-by-side analysis (per-second neural timeline + insights + verdict) ready for the results UI to render.

The full product spec lives in [`DESIGN.md`](./DESIGN.md). Implementation assumptions and research notes are in [`ASSUMPTIONS.md`](./ASSUMPTIONS.md).

```
[ MP4 A ] ─┐                ┌── /process_video_timeline ──> brain_a.json ─┐
           ├──> /api/analyze ─┤                                              ├─> Gemini ──> insights + verdict ──> results JSON
[ MP4 B ] ─┘  (this repo)    └── /process_video_timeline ──> brain_b.json ─┘
                                  TRIBE service (this repo, GPU)              Aesthesis app (this repo, CPU)
```

## Two services

| Service | Path | Role | Runtime |
|---|---|---|---|
| **TRIBE service** | `tribe_service/` | Wraps `tribev2.demo_utils.TribeModel` as an HTTP API. Returns per-TR brain frames + window composites for an MP4. | GPU (Modal A100/H100, `keep_warm=1` per D2) |
| **Aesthesis app** | `aesthesis_app/` | Public API. `POST /api/analyze` accepts two MP4 uploads, calls the TRIBE service serially, runs the Gemini synthesizer, returns the final results JSON. | CPU |

Both services have a built-in **mock mode** (`TRIBE_MOCK_MODE=1`, `GEMINI_MOCK_MODE=1`) that returns deterministic synthetic data. The full pipeline runs end-to-end on a laptop with no GPU and no API keys — useful for frontend development and CI.

## Quick start (mock mode, no GPU required)

```bash
# Install dev deps
pip install -e ".[dev]"
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run TRIBE service in mock mode (port 8001)
cd tribe_service
TRIBE_MOCK_MODE=1 uvicorn tribe_neural.api:app --port 8001

# In another shell, run the Aesthesis app (port 8000)
cd aesthesis_app
TRIBE_SERVICE_URL=http://localhost:8001 GEMINI_MOCK_MODE=1 \
  uvicorn aesthesis.main:app --port 8000

# Smoke test
curl -X POST localhost:8000/api/analyze \
  -F "video_a=@demo_a.mp4" \
  -F "video_b=@demo_b.mp4" \
  -F "goal=evaluate the signup flow"
```

## Production (Modal + Gemini)

Set up the TRIBE GPU service on Modal and point the app at it:

```bash
# Deploy TRIBE service (one-time)
cd tribe_service
modal deploy modal_app.py
# → records the URL like https://your-org--aesthesis-tribe-process-video-timeline.modal.run

# Run Aesthesis app
cd aesthesis_app
export TRIBE_SERVICE_URL=https://your-org--aesthesis-tribe-process-video-timeline.modal.run
export GEMINI_API_KEY=<your-google-key>
uvicorn aesthesis.main:app --port 8000
```

## Logging

Every step in both services emits structured logs via stdlib `logging` with `extra={...}` fields. Set `LOG_LEVEL=DEBUG` to surface every shape check, every external call, every timing measurement. Useful trace IDs:

- `run_id` — propagates across both services for the lifetime of one `/api/analyze` call
- `version` — `"A"` or `"B"`, identifies which video a log line is about
- `step` — pipeline phase (`validate`, `tribe`, `events`, `gemini.insights`, `gemini.verdict`, `output`)

```bash
LOG_LEVEL=DEBUG uvicorn aesthesis.main:app
```

## Layout

```
.
├── DESIGN.md               # full product spec (read first)
├── ASSUMPTIONS.md          # research log + decisions I had to make
├── TODOS.md                # v2 deferred work
├── CHANGELOG.md
├── VERSION
├── pyproject.toml
├── requirements-tribe.txt  # TRIBE GPU container deps
├── requirements-app.txt    # Aesthesis app deps
├── requirements-dev.txt    # test deps
├── tribe_service/
│   ├── modal_app.py
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── tribe_neural/
│       ├── api.py
│       ├── worker.py
│       ├── pipeline.py
│       ├── init_resources.py
│       ├── tribe_runner.py
│       ├── constants.py
│       ├── validation.py
│       ├── logging_config.py
│       └── steps/
├── aesthesis_app/
│   ├── pyproject.toml
│   └── aesthesis/
│       ├── main.py
│       ├── config.py
│       ├── logging_config.py
│       ├── validation.py
│       ├── tribe_client.py
│       ├── events.py
│       ├── screenshots.py
│       ├── synthesizer.py
│       ├── orchestrator.py
│       ├── output_builder.py
│       ├── prompts.py
│       └── schemas.py
└── tests/
```

## What's not in this PR

- **Step 1 (Capture)** — BrowserUse + screen recorder + live frame streamer. Per the user's request, this PR is "backend only, second stage." Capture is Phase 2 in DESIGN.md §12.
- **Frontend** — the Next.js wizard UI is not in this repo yet. The `output_builder` defines the JSON contract the UI will consume.
- **Real TRIBE inference verification** — verified that the API surface matches `tribev2`'s public demo notebook. Actually loading the model and confirming VRAM headroom is the Phase 0 spike (DESIGN.md §5.15.6) and requires GPU access.
