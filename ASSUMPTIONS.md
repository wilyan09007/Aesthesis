# ASSUMPTIONS.md — Backend Step 2 (Assess) implementation

> Generated alongside the v0.1.0.0 backend ship. Records every assumption I made when DESIGN.md was silent or contradictory, every external API surface I had to verify by research, and every choice I made about scope, dependency policy, or testing strategy.
>
> If you disagree with anything below, this is the file to argue with — DESIGN.md stays clean as the spec.

## 1. What "ship the backend, second stage" was scoped to

The user's request: *"backend only, entire second stage including mp4 to tribe v2 to analysis model to output page data."*

Concretely shipped:

1. **TRIBE service** (`tribe_service/`) — wraps `tribev2.demo_utils.TribeModel` as a FastAPI app. Returns the per-TR brain timeline + sliding-window composites for one MP4. Modal-deployable (`modal_app.py`), Docker-deployable (`Dockerfile`).
2. **Aesthesis app backend** (`aesthesis_app/`) — FastAPI orchestrator. `POST /api/analyze` accepts two MP4 multipart uploads, validates them, calls TRIBE serially per D6, runs the Insight Synthesizer (Gemini 2.0 Flash), assembles the final results-page JSON.
3. **Tests** — 53 passing tests covering all 8 per-TR composites, all 6 window composites, ROI extract, timeline assembly, event extraction, validation fallbacks, and an end-to-end smoke test that runs the entire pipeline against an in-process TRIBE service.
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

`init_resources.py::_try_load_real_masks` reads those `.npy` files from disk. If any are missing it raises with a hint pointing at `scripts/generate_weights.py`.

**Mock mode** (`TRIBE_MOCK_MODE=1`): synthesizes plausible masks deterministically (`_synthesize_mock_masks`) so the entire pipeline (and tests) runs without nilearn or any real atlas data. The mask sizes roughly match published Yeo-7 fractions so per-ROI weight sums are non-trivial and downstream composites get realistic-looking input.

**The `scripts/` directory itself is not in this PR.** DESIGN.md §5.12 lists it as part of the long-term file layout; building the actual `generate_weights.py` / `project_signatures.py` / `validate_signatures.py` is Phase 0 / GPU-required work and out of scope for "backend only, second stage." The project structure leaves a clear hook (`init_resources._try_load_real_masks`) for those scripts to plug into.

## 4. Neurosynth weight maps — same story

DESIGN.md §5.8 + `NEUROSYNTH_TERMS = ("fear", "reward", "uncertainty", "conflict", "social", "motor", "memory")`. We expect `data/neurosynth_weights.npz` with one array per term, shape `(20484,)`, non-negative.

Real-mode loader: `init_resources._try_load_real_weight_maps`. Mock-mode loader: `_synthesize_mock_weight_maps` — gamma-distributed (right-skewed, non-negative) which matches the empirical shape of Neurosynth ALE weight maps.

**VIFS / PINES signatures** are loaded if present (`data/vifs_surface.npy`, `data/pines_surface.npy`). They are wired through to `Resources` but the v1 ROI extract doesn't subtract VIFS yet — that's the parcel-subset post-processing TODO documented in `TODOS.md` v2 §1.

## 5. Insight Synthesizer — Gemini choices

DESIGN.md §4.5 + R1 specify the prompt verbatim. I copied the prompt + JSON schema into `aesthesis/prompts.py` exactly as written.

**Model choice:** `gemini-2.0-flash` for both insights and verdict by default. DESIGN.md §4.5 mentions "Gemini 2.5 Pro for final demo polish if Flash signal-to-noise isn't quite there." I parameterized via `GEMINI_MODEL_INSIGHTS` and `GEMINI_MODEL_VERDICT` env vars so swapping is one line of config.

**API surface:** I used the `google-generativeai` SDK (`google.generativeai.GenerativeModel.generate_content_async`) with `response_mime_type="application/json"`. JSON mode is the safest path — Gemini guarantees parseable JSON output. I also strip optional ```` ```json ... ``` ```` fences in case JSON mode glitches.

**Image attachment:** for each event, the per-event screenshot is read as raw JPEG bytes and passed as an inline image part alongside the text prompt. DESIGN.md §4.5 step 2 specifies `screenshot_b64` in the input JSON; in the actual SDK call, raw bytes via `{"mime_type": "image/jpeg", "data": img}` is the modern equivalent and saves a base64-decode pass on the model side.

**Mock mode** (`GEMINI_MOCK_MODE=1` or no `GEMINI_API_KEY`): synthesizer emits one believable insight per event, deterministically. `cited_brain_features` are populated from each event's primary ROI + `co_events`. The mock verdict picks the winner by majority of aggregate metrics. This lets the orchestrator + `output_builder` + smoke test all run without an API key.

**Falls back to mock on:**
- `_call_gemini` returns `{}` (malformed JSON)
- Insight construction raises `pydantic.ValidationError`
- `google-generativeai` not installed

These all log a WARNING but never raise — partial results beat a 500.

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

## 8. Mock-mode contract (the load-bearing dev affordance)

The user asked for verbose logging "for debugging purposes." A dev who can't reproduce the full pipeline locally has nothing to debug. So I built dual mock modes:

- `TRIBE_MOCK_MODE=1` → `MockTribeRunner` produces `(n_TRs, 20484)` synthetic predictions whose shape and approximate magnitude resemble TRIBE output. `n_TRs` is derived from the video's actual duration if ffmpeg is around; defaults to 30 otherwise (~45s clip — matches the canonical 30s-clip demo per DESIGN.md D7 with a small TRIBE warmup).
- `GEMINI_MOCK_MODE=1` → synthesizer skips Gemini entirely.

Together, the mock modes mean **any developer with `pip install pytest fastapi numpy pydantic httpx` can run the entire backend end-to-end on a laptop**. CI, local dev, frontend integration all run in mock mode. The only thing mock mode doesn't do is actually predict brain activity — but everything downstream of TRIBE (event extraction, composite math, JSON shape, FastAPI surface) runs against real data flowing through the real code paths.

Mock-mode synthetic predictions include:
- Per-vertex Gaussian noise (low base level)
- Low-frequency sinusoidal drift across random vertex subsets (so dominant flips happen)
- 1-3 sharp synthetic spikes (so spike-detection has something to find)

This is enough to exercise every event type, every composite, every gate.

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
- Both videos produce identical mock signal → verdict mock returns "tie" deterministically. The real-Gemini verdict prompt explicitly says "if the result is ambiguous, return tie" — same behavior.
- Mock TRIBE detects `mock=True` in its response; orchestrator propagates this onto the top-level `AnalyzeResponse.mock` field so the frontend can banner-mark mock results clearly during dev.

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
    "mock": bool,
}
```

`TimelineSummary` strips the heavy fields from the raw TRIBE response — we DON'T ship per-vertex predictions to the browser (20484 floats × n_TRs × 2 versions = several MB of JSON nobody needs). The frontend's chart only needs `roi_series` + `composites_series` + `windows`.

If the frontend wants the full per-TR `frames` array (with per-frame `co_movement` and `spikes` and `dominant_shift` flags) for fancier visualizations, that's an easy `output_builder` change — I left a comment.

## 14. Things I didn't do but DESIGN.md mentions

- **VIFS subtraction in `trust_affinity`** — DESIGN.md §5.16.2 row 4 specifies `_Default_ ∩ vmPFC voxels − 0.5 × VIFS signature`. The vmPFC parcel-subset is deferred to v2 per D5. The VIFS subtraction is half-built (the signature loads, but `extract_all` doesn't use it). I did NOT add the subtraction term because the v1 `NETWORK_KEYS_UX` simplification keeps `trust_affinity` at the network level — pairing it with parcel-subset work is the cleaner v2 patch.
- **Subcortical extension (§5.17)** — placeholder in DESIGN.md, captured in `TODOS.md`. I left the runner abstraction in a place where adding `run_subcortical=True` later doesn't disturb the rest of the pipeline.
- **`scripts/generate_weights.py`, `scripts/project_signatures.py`, `scripts/validate_signatures.py`** — DESIGN.md §5.8. These are GPU/network-required one-shot data generators. Stubs not included; `init_resources.py` raises a clear error pointing at them when real-mode artifacts are missing.

## 15. Test strategy

The 53 tests split into:

| File | What it tests | Runs without |
|---|---|---|
| `test_tribe_composites.py` | All 8 per-TR composites + all 6 window composites | numpy only |
| `test_tribe_extract_roi.py` | `extract_all` shape, z-score, visual_fluency hook, mask sizing | numpy only |
| `test_tribe_timeline.py` | `build_timeline` structure (frames + windows + roi_series) | numpy only |
| `test_app_validation.py` | Upload validation + ffmpeg-missing fallback | nothing extra |
| `test_app_events.py` | Each event type fires correctly + cap-aware truncation | nothing extra |
| `test_integration_smoke.py` | Full `/api/analyze` flow with in-process TRIBE app | fastapi + httpx |

Everything passes against fully-mocked TRIBE + fully-mocked Gemini. The only thing the test suite doesn't exercise is the real GPU path — that's the Phase 0 spike per DESIGN.md §5.15.6.

## 16. What you should do next

1. **Run the Phase 0 spike** (DESIGN.md §5.15.6) — verify on real GPU that `tribev2.demo_utils.TribeModel.predict(events=df)` actually returns the shape I assumed, with the timing I assumed.
2. **Build the data-prep scripts** under `scripts/` — Schaefer mask projection, Neurosynth ALE meta-analysis, VIFS/PINES signature projection. ~35 min of one-time work.
3. **Deploy to Modal** — `cd tribe_service && modal deploy modal_app.py`. First run will take ~35 min while the data prep runs on the volume.
4. **Wire up the frontend** to `POST /api/analyze` — the response shape is pinned in `aesthesis/schemas.py::AnalyzeResponse`.
5. **Then layer in Step 1 (Capture)** — DESIGN.md §12 Phase 2.

If anything in this file feels wrong, push back here and I'll revise. The idea is that DESIGN.md stays the spec and ASSUMPTIONS.md tracks the diff between spec and code.
