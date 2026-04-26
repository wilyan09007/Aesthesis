# Changelog

All notable changes to Aesthesis are recorded here. Format inspired by Keep a Changelog; versions follow the project's 4-digit `MAJOR.MINOR.PATCH.MICRO` scheme.

## [0.2.0.0] - 2026-04-25

### Added

- **Frontend ↔ backend wired up end to end.** Selecting two MP4s in `aesthesis-app/` now POSTs them to the FastAPI backend at `/api/analyze`, watches the (real) 12–25s pipeline, and renders the live brain timeline + insights + verdict. The `goResults` shortcut and `lib/mockData.ts` are gone.
- **`aesthesis-app/lib/api.ts`** — typed client for `/api/analyze`. `AbortController` cancellation, run-id propagation via `X-Aesthesis-Run-Id` response header, server-timing via `X-Aesthesis-Elapsed-Ms`, structured `console.info` logging keyed on the truncated run id, typed `AnalyzeError(message, status, body, runId)` that unwraps FastAPI `ValidationFailure` detail bodies for human-readable messages.
- **`aesthesis-app/lib/adapt.ts`** — backend `TimelineSummary { roi_series, tr_duration_s }` → `Frame[]` for the recharts/Brain3D consumers, plus `Verdict.summary_paragraph` → `verdict.summary` for the panel. One adapter call on response; components stay backend-shape-agnostic.
- **CORS on the backend.** `CORSMiddleware` reads origins from `CORS_ALLOW_ORIGINS` (defaults cover the Next.js dev server). Preflight + actual POST now succeed cross-origin. The two `X-Aesthesis-*` headers are listed under `expose_headers` so JS can read them.
- **HTTP request middleware** on the backend logs every request's method/path/status/elapsed_ms boundary.
- **Network-aware progress UX in `AnalyzingView`.** The synthetic 4-stage progress bar now stalls at 95% until the response arrives, surfaces a backend error panel with run-id + Retry/Start-Over when the pipeline fails, and labels the last stage as "waiting on Gemini…" to match what's actually happening.
- **`ASSUMPTIONS.md` §17** — full research log for the wire-up: Next.js 16 / React 19 verification (`node_modules/next/dist/docs/`), the Pydantic↔TS type alignment, the network-call + retry strategy, CORS choices, and what's still unverified end-to-end.

### Changed

- **`aesthesis-app/lib/types.ts`** is now a 1:1 mirror of `aesthesis_app/aesthesis/schemas.py`. Backend Pydantic is the single source of truth — divergence fails loudly at build time.
- **`/api/analyze` response carries headers** `X-Aesthesis-Run-Id` and `X-Aesthesis-Elapsed-Ms` so cross-tier debugging is `grep run_id` across browser + uvicorn logs.

### Removed

- **`aesthesis-app/lib/mockData.ts`** — per the project's no-mocks rule, fixture data designed to ship to production has no place in `lib/`. The frontend now only renders real backend output.

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
