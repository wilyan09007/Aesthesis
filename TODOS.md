# TODOS — Aesthesis

Items deferred from earlier reviews. Pull from this list when v1 is shipping cleanly.

---

## v2: Parcel-subset masks for `trust_affinity` / `aesthetic_appeal` / `surprise_novelty`

**Source:** `/plan-eng-review` decision D5 (2026-04-25). See DESIGN.md §15.

**What:** Build Schaefer 400 atlas parcel-subset masks so 3 of the 8 ROIs match their published anatomical signatures instead of the v1 simplified network-level mappings.

**Why:** The v1 `NETWORK_KEYS_UX` simplifies anatomy for these 3 keys. Concretely:
- `trust_affinity` should be vmPFC parcels minus VIFS-scaled fear; v1 uses the entire Default network weighted by Neurosynth `social`. Different signal.
- `aesthetic_appeal` should be Vessel-style PCC/mPFC subset of Default; v1 uses all of Default + Limbic.
- `surprise_novelty` should be SalVentAttn ∩ dACC weighted by Neurosynth `memory`; v1 uses all of SalVentAttn + Cont without the weight.

v1 signals are directionally correct but magnitude-attenuated and not literature-precise.

**Pros:**
- 3 of 8 ROIs become anatomically-precise rather than network-broad
- More defensible at hackathon judging or in a writeup ("we isolated vmPFC parcels per Mende-Siedlecki 2015" beats "we aggregated Default Mode Network activity")
- Higher signal-to-noise for those 3 ROIs (less noise from non-target activity)
- Closer match to published literature

**Cons:**
- ~6 hours of careful work (2hr atlas research + 2hr code + 2hr testing)
- Adds 3 new mask files + a small `extract_all` post-step (VIFS subtraction for trust)
- Doesn't change demo flow or UI

**Context:**
- Schaefer 400 atlas labels each parcel by anatomical region (e.g., `LH_Default_PFC_3`). Pull the labels from the released atlas file.
- Trust mapping reference: Mende-Siedlecki et al. 2015 (face trust amygdala meta), FeldmanHall et al. 2012 (vmPFC/amygdala trust).
- Aesthetic mapping reference: Vessel et al. 2019 *PNAS* (DMN aesthetic appeal — specifies PCC/mPFC subset).
- Surprise mapping reference: Sinclair et al. 2021 (anterior hippocampus + dACC for prediction error in naturalistic video).
- VIFS signature already loaded by the deployment per §5.5 step 7.
- No model training involved — just selecting subsets of existing TRIBE voxel output.

**Where to start:** read the Schaefer 400 atlas labels file, identify the parcel indices for vmPFC, PCC/mPFC, and dACC. Add 3 new boolean masks during `init_resources`. Update `NETWORK_KEYS_UX` to support parcel-subset keys (or add a parallel `PARCEL_KEYS_UX` dict). Modify `extract_all` to handle parcel-subset weights. Add a post-step that subtracts scaled VIFS from `trust_affinity`.

**Depends on / blocked by:** v1 shipping. No other dependencies.

---

## v2: Subcortical extension (§5.17 placeholder)

**Source:** Verified during `/plan-eng-review` research (2026-04-25). DESIGN.md §15.

**What:** Expose TRIBE v2's subcortical voxel predictions (8,802 voxels in addition to the 20,484 cortical, verified from the released model). Add `run_subcortical=True` to the inference call; build subcortical ROI masks for NAcc, amygdala (BLA/CeA), anterior hippocampus, VTA.

**Why:** Three of our 8 ROIs are currently cortical proxies for fundamentally subcortical signals:
- `reward_anticipation` — cortical projection of Limbic network. The actual NAcc signal lives subcortically. Adding NAcc directly would meaningfully strengthen the "wanting" signal.
- `trust_affinity` — needs amygdala suppression term per Mende-Siedlecki 2015 (paired with the parcel-subset work above).
- `surprise_novelty` — anterior hippocampus + VTA are subcortical and carry the prediction-error signal.

**Pros:**
- Stronger demo signal on the 3 most decision-relevant ROIs for UX
- More defensible scientifically — published trust/reward/surprise research points subcortically; cortical-only is a known compromise
- Synergistic with the parcel-subset work above (do them together if doing both)

**Cons:**
- Increases per-inference VRAM and wall time slightly (more output to project + aggregate)
- Subcortical anatomy is its own atlas (different from Schaefer 400 cortical) — need to learn it
- The TRIBE `run_subcortical` API surface is documented but not exercised by the YNeurotrading reference, so the spike will be longer

**Context:** TRIBE v2 has subcortical capability built in. Total brain output with subcortical = 29,286 voxels (20,484 cortical + 8,802 subcortical) per timestep.

**Where to start:** read the subcortical atlas TRIBE uses (likely a standard one — check the model config). Build masks for NAcc, BLA, CeA, anterior hippocampus, VTA. Add `run_subcortical=True` to the `predict()` call. Update `extract_all` to merge subcortical signal into the relevant ROIs.

**Depends on / blocked by:** v1 shipping. Synergistic with parcel-subset masks above — would be efficient to do both in one v2 push.

---

## v2: E2E test coverage for panel state machine + WebSocket reconnect

**Source:** `/plan-eng-review` test coverage diagram (2026-04-25).

**What:** Playwright tests for the full frontend state machine (idle → live → captured → results) and WebSocket disconnect/reconnect during a run.

**Why:** v1 covers the critical SIGKILL regression test and unit tests for composites. The frontend state machine is currently untested; if a transition breaks (e.g., `captured` panel never gets the MP4 URL), the demo silently shows a frozen frame forever.

**Pros:** catches state machine bugs that unit tests can't see; covers the WebSocket reconnect path which is currently "show frozen frame" with no test.

**Cons:** Playwright + WebSocket testing is fiddly (timing, mocking the backend). ~4 hours of test scaffolding.

**Context:** state machine spec in DESIGN.md §4.6. WebSocket spec in §4.2b.

**Where to start:** Playwright config + a fixture that mocks `/api/run` and the WebSocket. Test each transition. Add a teardown that kills the WebSocket and verifies UI stays sane.

**Depends on / blocked by:** v1 ships first; eval harness from R6 is a similar test infrastructure investment.

---

## v2: Eval suite expansion beyond the 3-pair Gemini eval

**Source:** `/plan-eng-review` recommendation R6 (2026-04-25).

**What:** Expand the Gemini Flash eval harness from 3 cached video pairs to a more diverse set covering different UI categories (SaaS landing, e-commerce, content site, dashboard, signup flow).

**Why:** v1 ships with 3 eval pairs — enough to catch regressions on the test cases but not enough to detect systematic prompt failures on novel categories.

**Pros:** prompt iteration becomes safer; can detect when a prompt change improves on category X but degrades on category Y.

**Cons:** each new eval pair = 1 hr to set up (record, capture brain timeline, hand-craft expected insights or scoring rubric).

**Context:** R6 scaffold from Phase 3.

**Where to start:** add 2 new pairs per category. Run before each prompt deploy. Compare insight quality scores.

**Depends on / blocked by:** v1 ships, prompt is stable enough that eval categories are useful.
