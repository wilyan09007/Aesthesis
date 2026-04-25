# Changelog

All notable changes to Aesthesis are recorded here. Format inspired by Keep a Changelog; versions follow the project's 4-digit `MAJOR.MINOR.PATCH.MICRO` scheme.

## [0.1.0.0] - 2026-04-25

### Added

- **Step 2 (Assess) backend pipeline end-to-end**: takes two MP4 screen recordings and returns the full results-page JSON (per-version brain timelines, insights, head-to-head verdict). Built per `DESIGN.md` §4.4 / §4.5 / §4.7 / §5.15 / §5.16.
- **TRIBE v2 service** (`tribe_service/`): FastAPI + ARQ + Redis architecture per §5.2. Exposes `/process_video_timeline`, `/enqueue_video_timeline`, `/job/{id}`, `/health`. Includes Modal deploy stub (`modal_app.py`).
- **TRIBE pipeline core**: `extract_all` against `NETWORK_KEYS_UX` (8 ROIs), per-TR composites (8), window composites (6), connectivity over `PAIRS_UX` (7 pairs), timeline builder with sliding-window overlay (`window_trs=4` default; `window_trs=6` for std-based composites per R2).
- **TRIBE runner** (`tribe_runner.py`): dispatches to `tribev2.demo_utils.TribeModel.predict`. Heavy deps (torch, V-JEPA 2, DINOv2, Wav2Vec-BERT, LLaMA 3.2) are loaded lazily on first call.
- **Aesthesis app backend** (`aesthesis_app/`): FastAPI `/api/analyze` endpoint accepting two MP4 multipart uploads + optional `goal`. Validates via `ffmpeg.probe` (codec, duration ≤30s, ≤1080p, ≤50MB). Forwards to TRIBE serially per D6, then runs the Insight Synthesizer.
- **Insight Synthesizer**: deterministic event extraction (spike, dominant_shift, sustained, co_movement, trough, flow, bounce_risk) per §4.5 step 1; Gemini 2.0 Flash insight call per version with the prompt + JSON schema verbatim from §4.5; Gemini verdict call with cross-version aggregate metrics. Failures (missing key, malformed JSON, schema mismatch) raise `SynthesizerError` and surface as a 500.
- **Output builder**: assembles the final results-page JSON contract that the (future) Next.js UI consumes.
- **Verbose structured logging** throughout both services using stdlib `logging` with `extra={...}` fields. `LOG_LEVEL=DEBUG` surfaces every step boundary, every shape check, every external call, and every timing measurement.
- **Tests**: pure-function unit tests for all 8 per-TR composites, all 6 window composites, `extract_all`, event extraction, and upload validation. Tests that exercise the full pipeline require a deployed TRIBE GPU service and a real `GEMINI_API_KEY`.
- **`ASSUMPTIONS.md`**: full research log — every TRIBE v2 API detail confirmed, every gap I had to fill, and every decision I made when DESIGN.md was silent or contradictory.
- **`README.md`**: build/run/test/deploy instructions for both services.
