# Changelog

All notable changes to Aesthesis are recorded here. Format inspired by Keep a Changelog; versions follow the project's 4-digit `MAJOR.MINOR.PATCH.MICRO` scheme.

## [0.3.0.0] - 2026-04-25

### Added

- **Real cortical brain visualization.** The Results page renders an actual fsaverage5 inflated cortical mesh (both hemispheres), colored per-parcel from TRIBE v2 z-scored activations, with drag-to-rotate. Replaces the prior icosahedron placeholder. Powered by `@react-three/fiber` (no new dependencies). Architecture mirrors Meta's TRIBE v2 demo (three.js + GLBs + per-vertex/per-face attributes) — see `ASSUMPTIONS_BRAIN.md` and `aesthesis-app/UIUX.md` §7.
- **`aesthesis-app/components/BrainCortical.tsx`** — loads two GLBs (left + right hemisphere) via drei `useGLTF`, reads custom `_PARCELID` and `_SULC` vertex attributes baked at build time, drives a per-vertex color buffer (`THREE.DynamicDrawUsage`) on every TR change, mixes in sulcal shading. Falls back to the placeholder `Brain3D` when `parcel_series` is null so deploys can stage independently.
- **`aesthesis-app/lib/colormap.ts`** — diverging blue↔red colormap (RdBu_r) anchored at z=0, with a smoothstep-based sulcal shading mix. Sign-aware: positive z reads warm, negative reads cool, zero is muted neutral.
- **`tribe_service/scripts/bake_parcel_map.py`** — projects the Schaefer-400 atlas onto fsaverage5 via `nilearn.surface.vol_to_surf`, masks cross-hemisphere assignments by parsing label names, writes `data/schaefer400_parcels.npy`. Runs once, output ships in the Modal data volume.
- **`tribe_service/scripts/bake_brain_glbs.py`** — bakes 4 GLBs (left/right × inflated/pial) under `aesthesis-app/public/brain/` with custom `_PARCELID` (uint16) and `_SULC` (float32) per-vertex attributes plus pre-computed normals. Hand-built buffer/bufferView/accessor graph via `pygltflib` to preserve the underscore-prefixed custom attribs through `THREE.GLTFLoader`.
- **`tribe_service/tribe_neural/steps/step2b_parcels.py`** — per-parcel reduction (z-scored across time) running in parallel to the existing 8-ROI pipeline. Loud failures on NaN/Inf inputs, shape mismatches, and unassigned parcels.
- **`TimelineSummary.parcel_series`** — new wire field on the `/api/analyze` response, shape `(n_TRs, 400)` float, optional. Backend emits it when the parcel map is loaded; frontend uses it to color the cortical mesh. ~32 KB per 30s clip.
- **`tests/test_step2b_parcels.py`** — real-data parcel-reduction tests (no mocks). Asserts the shape contract, z-score properties, empty-parcel handling, and loud failure on NaN/Inf inputs. The artifact-dependent test fails with a runbook command when the Schaefer parcel map hasn't been baked.
- **`ASSUMPTIONS_BRAIN.md`** — research log + architectural decisions for the cortical brain implementation. Covers fsaverage5 vertex order, Schaefer projection caveats, GLB custom-attribute survival through GLTFLoader, three.js BufferAttribute update patterns, and the no-mocks testing strategy.
- **`aesthesis-app/UIUX.md`** — comprehensive UI/UX implementation plan with the brain-visualization phase plan (§7), cross-referencing the Meta TRIBE v2 demo's confirmed stack (hand-rolled three.js, two-tier mesh GLBs, per-face shader, pre-baked color streams).

### Changed

- **Insight timestamps clamped to video bounds.** `aesthesis_app/aesthesis/synthesizer.py` now passes `duration_s` into both Gemini prompts and snaps every returned `timestamp_range_s` into `[0, duration_s]`. Insights whose start lies past the video end are dropped with a logged warning. Fixes the bug where insight cards pointed past the end of the video player.
- **Video panel sized to `50vw × 50vh`.** `aesthesis-app/components/VideoPlayer.tsx` resolves the duelling `flex-1` + `aspect-video` feedback loop that made the frame fill the viewport. Inner `flex-1` removed; outer panel given an explicit `height: 50vh`.
- **Brain panel paired with the video.** `aesthesis-app/components/ResultsView.tsx` wraps the brain in its own `50vh × 50vh` panel with header (`Neural state · t = X.Xs`) and a "drag to rotate" caption. Faint pulsing-orb fallback during lazy-load instead of a generic spinner.
- **Brain colormap sign-aware.** `aesthesis-app/components/Brain3D.tsx` squashes z-scores through `tanh(z * 0.7)` and applies asymmetric warm/cool shifts so negative activations read distinctly from "no signal." OrbitControls rotation enabled (zoom/pan still off).
- **`tribe_service/tribe_neural/init_resources.py`** loads the Schaefer parcel map at worker boot when present; logs loudly when absent so the cortical-brain rendering's degradation path is obvious in startup logs.

### Fixed

- **`dev.sh` CRLF safety.** `.env` files with Windows line endings no longer leak `\r` into env var values (which broke `TRIBE_SERVICE_URL` curl checks). Strips CR via `tr -d '\r' < .env` process substitution before sourcing.
- **`dev.cmd`** — Windows entry point. Locates Git for Windows' `bash.exe` and hands `dev.sh` off to it. Skips WSL bash because `dev.sh` expects Windows-side `python` / `npm` / `taskkill.exe`.

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
