# ASSUMPTIONS.md — Backend Step 2 (Assess) implementation

> Generated alongside the v0.1.0.0 backend ship. Records every assumption I made when DESIGN.md was silent or contradictory, every external API surface I had to verify by research, and every choice I made about scope, dependency policy, or testing strategy.
>
> If you disagree with anything below, this is the file to argue with — DESIGN.md stays clean as the spec.

> **Pivot note (2026-04-25):** Aesthesis pivoted from A/B comparison to single-video analysis. See DESIGN.md §17 for the new contract. Below, every "two videos" / "version A / version B" / "verdict" reference reflects the pre-pivot shape. The TRIBE service architecture (§2 onwards) is unchanged — it was always per-video.

## 1. What "ship the backend, second stage" was scoped to

The user's request: *"backend only, entire second stage including mp4 to tribe v2 to analysis model to output page data."*

Concretely shipped:

1. **TRIBE service** (`tribe_service/`) — wraps `tribev2.demo_utils.TribeModel` as a FastAPI app. Returns the per-TR brain timeline + sliding-window composites for one MP4. Modal-deployable (`modal_app.py`), Docker-deployable (`Dockerfile`).
2. **Aesthesis app backend** (`aesthesis_app/`) — FastAPI orchestrator. `POST /api/analyze` accepts two MP4 multipart uploads, validates them, calls TRIBE serially per D6, runs the Insight Synthesizer (Gemini 2.0 Flash), assembles the final results-page JSON.
3. **Tests** — pure-function unit tests covering all 8 per-TR composites, all 6 window composites, ROI extract, timeline assembly, event extraction, and upload validation. Tests that exercise the full pipeline require a deployed TRIBE GPU and a real `GEMINI_API_KEY` — there is no mock/smoke layer.
4. **Verbose structured logging** — every step in both services emits log lines with `run_id`, `version`, `step`, and `elapsed_ms` fields. `LOG_LEVEL=DEBUG` surfaces shape checks and per-step timing.

Explicitly **not** shipped (per "backend only, second stage"):

- Step 1 (Capture): BrowserUse, screen recorder, live frame streamer
- Frontend: the Next.js wizard UI is not in this repo yet
- Phase 0 spike (`§5.15.6`): real GPU verification — requires a GPU I don't have

## 2. TRIBE v2 API surface — what I had to verify

DESIGN.md cites the TRIBE v2 demo at https://github.com/facebookresearch/tribev2/blob/main/tribe_demo.ipynb but doesn't pin the API. I read the published demo notebook to confirm the call shape that `tribe_runner.py` depends on:

```python
model = TribeModel.from_pretrained("facebook/tribev2", cache_folder=CACHE_FOLDER)
df = model.get_events_dataframe(video_path=video_path)
preds, segments = model.predict(events=df)
# preds.shape == (n_timesteps, n_vertices) == (53, 20484) for an ~80s clip
```

Confirmed:
- `from_pretrained` takes a `cache_folder` kwarg, not the older `cache_dir`.
- `get_events_dataframe` overloads on `text_path=` / `audio_path=` / `video_path=`.
- `predict` returns a tuple `(preds, segments)` — we discard `segments` (we don't need word-level alignments downstream).
- Output is `np.ndarray` (or torch.Tensor that we `.cpu().numpy()`). We `.astype(np.float64)` after either.
- Shape is `(n_timesteps, 20_484)` — fsaverage5 cortical mesh, both hemispheres.
- The 5s hemodynamic offset is **applied by TRIBE itself** (D3 in DESIGN.md). Output `t * TR_DURATION` already aligns to stimulus time. We don't shift.

**Discrepancy with DESIGN.md:** the demo notebook describes one prediction per second of stimulus. DESIGN.md uses `TR_DURATION = 1.5s`. This is consistent: TRIBE's actual training TR is 1.49s; DESIGN.md rounds to 1.5s; the notebook's "one per second of stimulus" is approximate prose. We standardize on `TR_DURATION = 1.5` in `constants.py` because every published TRIBE figure uses that number.

**Edge-TR trim** (`constants.EDGE_TR_TRIM = 2`): DESIGN.md §5.6 #4 says "drop last 2 TRs" because of a Transformer boundary artifact. §5.15.5 marks this as "VERIFY whether still applies for video path." I default to applying the trim (conservative) and ship `EDGE_TR_TRIM_DISABLE=1` env var so the Phase 0 spike can re-validate cheaply.

**Sources:**
- [tribev2 GitHub](https://github.com/facebookresearch/tribev2)
- [tribe_demo.ipynb](https://github.com/facebookresearch/tribev2/blob/main/tribe_demo.ipynb)
- [HF model card](https://huggingface.co/facebook/tribev2)

## 3. Yeo / Schaefer atlas masks — what real-mode resources need

DESIGN.md §5.5 step 5 says: "Build Schaefer 400 masks: fetch volumetric atlas + fsaverage5; project via `vol_to_surf`; substring-match labels."

**Assumption:** at production deploy time, somebody runs a one-shot script (`scripts/generate_weights.py` per §5.8) that:

1. Fetches the Schaefer 400 atlas via `nilearn.datasets.fetch_atlas_schaefer_2018(n_rois=400, yeo_networks=7)`.
2. Projects it to fsaverage5 via `nilearn.surface.vol_to_surf`.
3. For each Yeo-7 network substring (`_Default_`, `_Limbic_`, `_Vis_`, `_Cont_`, `_DorsAttn_`, `_SomMot_`, `_SalVentAttn_`), produces a boolean mask of shape `(20484,)` and saves to `data/masks/<substring>.npy`.

`init_resources.py::_load_masks` reads those `.npy` files from disk. If any are missing it raises with a hint pointing at `scripts/generate_weights.py`.

**The `scripts/` directory itself is not in this PR.** DESIGN.md §5.12 lists it as part of the long-term file layout; building the actual `generate_weights.py` / `project_signatures.py` / `validate_signatures.py` is Phase 0 / GPU-required work and out of scope for "backend only, second stage." The project structure leaves a clear hook (`init_resources._try_load_real_masks`) for those scripts to plug into.

## 4. Neurosynth weight maps — same story

DESIGN.md §5.8 + `NEUROSYNTH_TERMS = ("fear", "reward", "uncertainty", "conflict", "social", "motor", "memory")`. We expect `data/neurosynth_weights.npz` with one array per term, shape `(20484,)`, non-negative.

Loader: `init_resources._load_weight_maps`. Tests construct synthetic weight maps inline (gamma-distributed, right-skewed non-negative — matches the empirical shape of Neurosynth ALE) for unit-testing `extract_all` in isolation.

**VIFS / PINES signatures** are loaded if present (`data/vifs_surface.npy`, `data/pines_surface.npy`). They are wired through to `Resources` but the v1 ROI extract doesn't subtract VIFS yet — that's the parcel-subset post-processing TODO documented in `TODOS.md` v2 §1.

## 5. Insight Synthesizer — Gemini choices

DESIGN.md §4.5 + R1 specify the prompt verbatim. I copied the prompt + JSON schema into `aesthesis/prompts.py` exactly as written.

**Model choice:** `gemini-2.0-flash` for both insights and verdict by default. DESIGN.md §4.5 mentions "Gemini 2.5 Pro for final demo polish if Flash signal-to-noise isn't quite there." I parameterized via `GEMINI_MODEL_INSIGHTS` and `GEMINI_MODEL_VERDICT` env vars so swapping is one line of config.

**API surface:** I used the `google-generativeai` SDK (`google.generativeai.GenerativeModel.generate_content_async`) with `response_mime_type="application/json"`. JSON mode is the safest path — Gemini guarantees parseable JSON output. I also strip optional ```` ```json ... ``` ```` fences in case JSON mode glitches.

**Image attachment:** for each event, the per-event screenshot is read as raw JPEG bytes and passed as an inline image part alongside the text prompt. DESIGN.md §4.5 step 2 specifies `screenshot_b64` in the input JSON; in the actual SDK call, raw bytes via `{"mime_type": "image/jpeg", "data": img}` is the modern equivalent and saves a base64-decode pass on the model side.

**Failure mode is loud:** missing `GEMINI_API_KEY`, missing `google-generativeai`, malformed JSON from Gemini, or schema mismatches all raise `SynthesizerError`. The orchestrator surfaces these as a 500 to the caller. There is no fallback to synthetic insights — a broken Gemini integration must show up at request time, not pass silently.

## 6. Aggregate metrics — how I picked the 8

DESIGN.md §4.5 step 3 gives a sample table of 8 metrics. I lifted it directly:

| Metric | Direction (higher is better?) | Where it comes from |
|---|---|---|
| `mean_appeal_index` | yes | per-TR composite, integrated |
| `mean_cognitive_load` | no (lower better) | raw ROI |
| `pct_reward_dominance` | yes | per-TR `dominant` flag |
| `pct_friction_dominance` | no | per-TR `dominant` flag |
| `friction_spike_count` | no | per-TR `spikes.friction_anxiety` |
| `motor_readiness_peak` | yes | raw ROI peak |
| `flow_state_windows` | yes | window composite triggers |
| `bounce_risk_windows` | no | window composite triggers |

Each metric carries an `edge_description` field that explains the direction so the frontend doesn't have to hard-code rules. (It's still the frontend's call how to render — we just hand it the data.)

## 7. Event-extraction caps and tier policy

DESIGN.md §4.5 step 1 says "8-15 events per 60s." I cap at `EVENT_CAP = 15` per video.

When more than 15 raw events are mined, the truncation is tier-aware:

1. **Always keep**: every `flow`, `bounce_risk`, and `trough` event. These are rare, high-signal, window-level patterns. Losing one means losing a category-defining moment.
2. **Fill remainder**: highest-magnitude `spike`/`co_movement`/`dominant_shift`/`sustained` events, with a soft cap of 4 per type so one noisy ROI doesn't dominate.
3. **Output is timestamp-sorted** so the frontend's per-event chart annotations come out in time order.

This is my interpretation of "preserve diversity" (the design doc says "8-15 events" but doesn't specify how to truncate when raw count exceeds the cap).

## 8. No mock mode

Earlier drafts of this backend shipped with `TRIBE_MOCK_MODE` and `GEMINI_MOCK_MODE` environment flags plus a `MockTribeRunner` and a synthetic Gemini synthesizer fallback. Those were removed: tests that pass against fake services give false confidence, and a "real GPU + real Gemini" requirement forces breakage to surface where it matters. The only synthetic data left in the codebase is the in-test mask/weight constructors used by `test_tribe_extract_roi.py` to unit-test the pure `extract_all` math in isolation — those are normal test fixtures, not runtime fakes.

## 9. ARQ optionality

DESIGN.md §5.2 says the GPU worker uses ARQ + Redis. In dev / laptop / CI environments ARQ isn't available. `tribe_neural/worker.py` wraps the ARQ import in a try/except — if it's missing, the FastAPI app skips the `/enqueue_video_timeline` and `/job/{job_id}` routes (returns 501) and runs `/process_video_timeline` synchronously. The synchronous path is the only one the Aesthesis app uses for v1 anyway (DESIGN.md §4.4 — async variant marked optional). On the GPU container ARQ is real and both paths work.

## 10. ffmpeg optionality

`aesthesis/validation.py` has a hard fallback when `ffmpeg-python` is missing — accept the upload on header-only check and log a warning. The TRIBE service will reject malformed files anyway (its `tribev2` call fails fast). This keeps the local dev loop functional on machines without ffmpeg.

`aesthesis/screenshots.py` has three tiers:
1. `ffmpeg-python` — preferred path
2. shell out to the `ffmpeg` CLI binary directly — works whenever the system has ffmpeg installed even without the Python wrapper
3. log + skip — events ship to Gemini without screenshots; insights still generate (Gemini's insight prompt explicitly says "if you can't tell from the screenshot why, say so")

This was a deliberate trade-off: I'd rather ship a partial result than a 500 error mid-demo because ffmpeg is missing on one container.

## 11. Edge-cases the orchestrator handles silently

- `extract_events` produces an empty list (TRIBE input was too quiet to spike) → synthesizer skips Gemini for that version, response carries `events: []` and `insights: []`. Verdict still computes off the aggregate metrics alone.
- TRIBE returns fewer than 4 TRs → window pass is skipped, frames pass still emits, response carries `windows: []`. Frontend can detect this (empty windows array) and render a warning.
- The real-Gemini verdict prompt explicitly says "if the result is ambiguous, return tie."

## 12. Per-vertex weight construction in `extract_all` — design note

DESIGN.md §5.16.7 says the `_Vis_ − 0.5 × _DorsAttn_` formula for `visual_fluency` "needs a post-step in `extract_all` (the dict can't express it)." I implemented this exactly — built the standard per-ROI weight vector first, computed `extract_all` over all 8 ROIs naively, then mutated `out["visual_fluency"]` to subtract `0.5 * dorsattn_mean_per_tr`. Z-scoring runs after the post-step.

The other ROIs that DESIGN.md flags as "magnitude-attenuated v1" (DESIGN.md §5.16 status block items 1-3) — `trust_affinity`, `aesthetic_appeal`, `surprise_novelty` — use the simplified `NETWORK_KEYS_UX` mapping verbatim from DESIGN.md §5.16.2. Per D5 (parcel-subset masks deferred to v2), this is "directionally correct, magnitude-attenuated" and is captured in `TODOS.md` for the post-v1 push.

## 13. Frontend contract

The `AnalyzeResponse` Pydantic model in `aesthesis/schemas.py` IS the contract. The frontend reads:

```python
response = {
    "meta": {"goal": str | None, "run_id": str, "received_at": iso8601},
    "a": VersionResult,  # video_url, duration_s, timeline, events, insights
    "b": VersionResult,
    "aggregate_metrics": [AggregateMetric, ...],
    "verdict": Verdict,
    "elapsed_ms": float,
}
```

`TimelineSummary` strips the heavy fields from the raw TRIBE response — we DON'T ship per-vertex predictions to the browser (20484 floats × n_TRs × 2 versions = several MB of JSON nobody needs). The frontend's chart only needs `roi_series` + `composites_series` + `windows`.

If the frontend wants the full per-TR `frames` array (with per-frame `co_movement` and `spikes` and `dominant_shift` flags) for fancier visualizations, that's an easy `output_builder` change — I left a comment.

## 14. Things I didn't do but DESIGN.md mentions

- **VIFS subtraction in `trust_affinity`** — DESIGN.md §5.16.2 row 4 specifies `_Default_ ∩ vmPFC voxels − 0.5 × VIFS signature`. The vmPFC parcel-subset is deferred to v2 per D5. The VIFS subtraction is half-built (the signature loads, but `extract_all` doesn't use it). I did NOT add the subtraction term because the v1 `NETWORK_KEYS_UX` simplification keeps `trust_affinity` at the network level — pairing it with parcel-subset work is the cleaner v2 patch.
- **Subcortical extension (§5.17)** — placeholder in DESIGN.md, captured in `TODOS.md`. I left the runner abstraction in a place where adding `run_subcortical=True` later doesn't disturb the rest of the pipeline.
- **`scripts/generate_weights.py`, `scripts/project_signatures.py`, `scripts/validate_signatures.py`** — DESIGN.md §5.8. These are GPU/network-required one-shot data generators. Stubs not included; `init_resources.py` raises a clear error pointing at them when real-mode artifacts are missing.

## 15. Test strategy

The tests split into:

| File | What it tests | Requires |
|---|---|---|
| `test_tribe_composites.py` | All 8 per-TR composites + all 6 window composites | numpy only |
| `test_tribe_extract_roi.py` | `extract_all` shape, z-score, visual_fluency hook, mask sizing | numpy only |
| `test_tribe_timeline.py` | `build_timeline` structure (frames + windows + roi_series) | numpy only |
| `test_app_validation.py` | Upload validation + ffmpeg-missing fallback | nothing extra |
| `test_app_events.py` | Each event type fires correctly + cap-aware truncation | nothing extra |

Pure-function tests run on numpy alone. Anything that exercises the full pipeline must hit the deployed TRIBE GPU and a live Gemini API — there is no smoke test built around synthetic data, by design.

## 16. What you should do next

1. **Run the Phase 0 spike** (DESIGN.md §5.15.6) — verify on real GPU that `tribev2.demo_utils.TribeModel.predict(events=df)` actually returns the shape I assumed, with the timing I assumed.
2. **Build the data-prep scripts** under `scripts/` — Schaefer mask projection, Neurosynth ALE meta-analysis, VIFS/PINES signature projection. ~35 min of one-time work.
3. **Deploy to Modal** — `cd tribe_service && modal deploy modal_app.py`. First run will take ~35 min while the data prep runs on the volume.
4. ~~Wire up the frontend~~ — done in v0.2.0.0; see §17 below.
5. **Then layer in Step 1 (Capture)** — DESIGN.md §12 Phase 2.

If anything in this file feels wrong, push back here and I'll revise. The idea is that DESIGN.md stays the spec and ASSUMPTIONS.md tracks the diff between spec and code.

## 17. Frontend wire-up (v0.2.0.0)

This section records every decision and bit of research from connecting the Next.js frontend (merged in by Vineel as `aesthesis-app/`) to the FastAPI backend. The goal was to replace `lib/mockData.ts` with real `/api/analyze` calls, with no mocks remaining anywhere in the project.

### 17.1 Stack — what's actually in `aesthesis-app/`

Read `aesthesis-app/AGENTS.md` first: *"This is NOT the Next.js you know."* Vineel is using a current-edge stack that postdates a lot of training data:

- `next@16.2.4` (App Router only, Turbopack default)
- `react@19.2.4`, `react-dom@19.2.4`
- `framer-motion@12`, `recharts@3.8`, `@react-three/fiber@9` + `@react-three/drei@10`
- `tailwindcss@4` via `@tailwindcss/postcss`
- `typescript@5`

What this meant in practice — verified by reading `node_modules/next/dist/docs/01-app/`:
- **Environment variables** (`02-guides/environment-variables.md`): the `NEXT_PUBLIC_*` rule is unchanged in Next 16 — a prefixed env var is inlined at build time into the browser bundle. So `NEXT_PUBLIC_AESTHESIS_API_URL` is the right knob for where the browser POSTs.
- **`fetch`** (`03-api-reference/04-functions/fetch.md`): the extended fetch in Next 16 still accepts the standard Web Fetch API. For our case (a client component in `app/page.tsx` doing a multipart upload from the browser) the fetch happens browser-side, so the Next.js cache extension doesn't apply — `cache: "no-store"` is set defensively because the response is a non-idempotent compute result that should never be cached.
- No App-Router-specific quirks bit us. `app/page.tsx` stays a `"use client"` component because the file selector + analyzing UI are all interactive.

### 17.2 Type alignment — backend Pydantic is the source of truth

The merged-in `aesthesis-app/lib/types.ts` had a simplified `AnalyzeResponse` shape (3-field `Insight`, `verdict.summary`, per-frame `frames[]`) that didn't match the real backend `AnalyzeResponse` from `aesthesis_app/aesthesis/schemas.py`. Specifically:

| Frontend (before)                          | Backend (Pydantic)                                                                              |
|---                                         |---                                                                                              |
| `Insight { timestamp_range_s, ux_observation, recommendation }` | `Insight { version, timestamp_range_s, ux_observation, recommendation, cited_brain_features, cited_screen_moment }` |
| `Verdict { winner, summary }`              | `Verdict { winner, summary_paragraph, version_a_strengths, version_b_strengths, decisive_moment }` |
| `VersionResult { frames, insights }`       | `VersionResult { version, video_url, duration_s, timeline: TimelineSummary, events, insights }` |
| (no equivalent)                            | `TimelineSummary { n_trs, tr_duration_s, roi_series, composites_series, windows, processing_time_ms }` |
| (no equivalent)                            | top-level `meta`, `aggregate_metrics`, `elapsed_ms`                                              |

The frontend was clearly designed against the mock fixture — Vineel hadn't seen the real schema. **Decision: Pydantic wins.** I rewrote `aesthesis-app/lib/types.ts` as a 1:1 mirror of `schemas.py`. Then I added `aesthesis-app/lib/adapt.ts` with one function, `adaptForResultsView()`, that derives the small "view shape" the components actually consume:

```ts
type ResultsViewData = {
  a: { frames: Frame[]; insights: Insight[]; duration_s: number }
  b: { frames: Frame[]; insights: Insight[]; duration_s: number }
  verdict: { winner; summary }   // mapped from summary_paragraph
  raw: AnalyzeResponse
}
```

Why an adapter and not a refactor of every component? `BrainChart`, `InsightCard`, and `VerdictPanel` all have polished props that match Vineel's mockData shape. Touching them all to read `data.a.timeline.roi_series` directly would be churn for no win — the adapter is 50 lines, runs once on response, and keeps every component code path identical.

`framesFromTimeline()` rebuilds `Frame[]` from `roi_series` + `tr_duration_s` defensively: it picks the longest series so a missing ROI doesn't truncate the X axis (zero-fills missing values).

### 17.3 Network call — what `lib/api.ts` does and why

The full `/api/analyze` round trip is 12–25s (ffmpeg validate + serial TRIBE GPU calls + two Gemini insight calls + Gemini verdict). That's a long browser fetch. Decisions:

- **Single round-trip**, not WebSocket / SSE / polling. The pipeline is already serial and the latency budget is dominated by GPU + Gemini, not browser wait. A streaming UX would require backend rework. We can revisit if/when the time budget grows.
- **`AbortController` lifecycle in `app/page.tsx`** — if the user navigates back during an in-flight request (or starts a second analysis), we cancel the first. Without this, an orphaned 25s response would write into stale state. Pattern: `abortRef.current?.abort()` before each new launch.
- **Multipart upload via `FormData`** — fields named exactly `video_a`, `video_b`, `goal` to match the FastAPI signature in `main.py::analyze`. Critically: don't set the `Content-Type` header manually. The browser writes `multipart/form-data; boundary=...` and overriding it with `multipart/form-data` (no boundary) breaks the multipart parser silently. The default behavior is correct; the temptation to "be explicit" is wrong.
- **Run-id propagation via response header.** The backend generates `run_id` (UUID) per request and now returns it as `X-Aesthesis-Run-Id`. The frontend `analyze()` reads it on response and tags every subsequent log line with the truncated id. Cross-tier debugging across browser console + uvicorn logs becomes a `grep run_id`.
- **Server-elapsed header `X-Aesthesis-Elapsed-Ms`** — also returned, exposed via CORS `expose_headers`. Lets us log "the network said 25.4s, the server said 24.7s, so 0.7s was wire" without instrumentation.
- **Typed error: `AnalyzeError(message, status, body, runId)`.** The orchestrator returns 400 with `ValidationFailure { field, error }` wrapped in FastAPI's `detail`; 502 for TRIBE failures; 500 for everything else. The client unwraps `detail` and prefers `field: error` if shaped that way, else falls back to `body.detail` string, else generic. The error message + run_id surface verbatim in `AnalyzingView`'s error panel — the user gets the exact backend message and a copy-pasteable run_id.

### 17.4 CORS

The browser hits `localhost:8000` from `localhost:3000` — that's a different origin, so without CORS the preflight `OPTIONS` request fails before the multipart `POST` ever lands. Added `fastapi.middleware.cors.CORSMiddleware` in `main.py` with origins driven by a new config field `cors_allow_origins` (env var `CORS_ALLOW_ORIGINS`, default covers the Next dev server). `allow_credentials=False` because we don't send cookies — that keeps wildcard-style configs legal if someone needs them in dev. `expose_headers` lists the two `X-Aesthesis-*` headers so JS can read them; without that line the headers exist on the response but `resp.headers.get(...)` returns `null` in the browser — a footgun I've stepped on before.

### 17.5 Progress UX during a 25s wait

`AnalyzingView` already had a synthetic 4-stage progress animation. I kept it but added a network-aware hold:

- The animation runs as before, but when it reaches stage 4 (the last one) and the network response *hasn't* arrived, progress sticks at 95% until it does.
- When the response arrives (resolved=true), the animation continues to 100% naturally.
- If the response errors, the progress bars are replaced with an error panel showing the backend message + run_id + Retry/Start-Over buttons.
- A subtitle line distinguishes "Pipeline takes 12–25s end-to-end" (in flight) vs. "Response received — finalizing UI" (resolved, animation catching up).

This is a better UX than either (a) a static "Loading..." spinner for 20s or (b) progress that finishes in 3s and then the user stares at a "complete" panel that hasn't actually completed. It does mean the progress is theatrical, not real — but real per-stage progress would require server-sent events from the orchestrator, which is out of scope here.

### 17.6 Scope deletions

- **`aesthesis-app/lib/mockData.ts`** is deleted. Per the project's no-mocks rule, fixture data designed to ship to production has no place in `lib/`. The only `MOCK_DATA` reference was in `app/page.tsx`'s `goResults` callback, now replaced with the real fetch path.
- **No frontend tests yet.** Vineel didn't add any and I'm not adding stub tests just to have green checks. When tests come in they should hit the real backend (with a fixture MP4 pair) per the no-mocks rule — i.e. integration tests, not mocked unit tests of the API client.

### 17.7 Things still unverified

- **End-to-end smoke run.** I built and typechecked the frontend (`npx next build`, `npx tsc --noEmit`, both pass). I did not run the full local stack (uvicorn + Next dev server + a real MP4 pair + a real Gemini key). The wiring is right by construction, but the first real request is the only proof — somebody needs to flip the lights on.
- **Run-id truncation.** The frontend logs use `run_id.slice(0, 8)` which is fine for grepping but loses uniqueness in pathological cases. Full run_id is preserved on `AnalyzeError.runId` and visible in the response header.
- **Browser file size limits.** A 50MB MP4 is fine for `FormData`, but on slow uplinks a 25s server-side budget plus ~20s upload becomes ~45s total. We don't currently surface upload progress separately from the analyzing animation.
