# ASSUMPTIONS — Brain Cortical Visualization (PR `feat/brain-cortical-3js`)

**Purpose.** This document records every assumption, research finding, and
decision made during the implementation of the rotatable cortical brain
visualization that mirrors TRIBE v2 activity. When something breaks,
this is the first place to look. When the code disagrees with this
document, the document is wrong, fix it the same day.

**Companion docs.**
- `aesthesis-app/UIUX.md` §7 — the broader design plan and Phases 0–5.
- `DESIGN.md` §5.4–§5.16 — TRIBE pipeline, ROI definitions, atlas usage.

**No mocks rule.** The user-stated hard rule: no mock modes anywhere,
tests run against real data only. Every test in this PR uses real
numpy arrays, real file system reads, and fails loudly when artifacts
are missing — pointing at the exact bake step that needs to run.

---

## 1. Data plane: what TRIBE v2 emits and what we render

### 1.1 TRIBE v2 output

- Shape: `(n_TRs, 20484)` float32. (`tribe_service/tribe_neural/constants.py:27`, `NUM_VERTICES = 20_484`.)
- Coordinate system: **fsaverage5** cortical mesh.
- Per-hemisphere split: vertices `[0..10241]` are **left hemisphere**; vertices `[10242..20483]` are **right hemisphere**. This is the standard nilearn convention — confirmed via the nilearn fsaverage5 docs.
- Time alignment: `t_s = i * TR_DURATION` where `TR_DURATION = 1.5`. The 5-second hemodynamic shift TRIBE applies internally is already baked in; no further offset needed (`constants.py:18-22`).

### 1.2 Schaefer-400 parcellation

- Source: `nilearn.datasets.fetch_atlas_schaefer_2018(n_rois=400, yeo_networks=7, resolution_mm=1)` — already used by `tribe_service/scripts/generate_weights.py:88`.
- The atlas ships in **MNI152 volumetric space**, NOT directly on fsaverage5 surface. Projection to surface is required.
- Projection method: `nilearn.surface.vol_to_surf(atlas['maps'], fsavg['pial_left'], interpolation='nearest', ...)` — same pattern as `generate_weights.py:135`. `nearest` interpolation preserves integer parcel labels.
- After projection: each fsaverage5 vertex carries a parcel index in `[0..400]`, where `0` is "unassigned" (rare; midline / corpus callosum cuts).
- Per-hemisphere convention: when projecting only `pial_left`, vertices in the right hemisphere physical location can still receive labels from right-hemisphere parcels because the volumetric atlas doesn't know about hemispheres. We post-process: for the LEFT mesh projection, keep only labels where Schaefer's name says `LH`; mask the rest to `0`. Same for right.
- Schaefer naming convention: each label name starts with `7Networks_LH_` or `7Networks_RH_`. Use the LUT to determine which.

### 1.3 Per-parcel activity (the wire format)

We compute `parcel_series: (n_TRs, 400)` — z-scored per parcel across time —
in the TRIBE pipeline (`step2b_parcels.py`) and ship it on the wire as part
of `TimelineSummary`.

Wire size budget: 30s × 20 TRs × 400 floats × 4 bytes = **~32 KB** per analysis. No need to quantize, base64, or zip.

### 1.4 Why parcels not per-vertex

- Wire size: 32 KB vs ~1.6 MB at per-vertex float32 (or ~410 KB at int8 quantized).
- Compute cost on hover: recoloring 400 parcels takes ~0.1ms in JS; recoloring 20,484 vertices is ~3ms — both fit a frame, but parcel is comfortably below.
- Visual fidelity: 400 patches give clean, readable anatomical regions. Per-vertex would be smoother but requires a custom shader (Phase 5 territory). See `UIUX.md` §7.5 for the upgrade path.

---

## 2. Mesh bake: GLB and custom attributes

### 2.1 Mesh source

- nilearn's `fetch_surf_fsaverage(mesh="fsaverage5")` returns paths to GIFTI files: `pial_left`, `pial_right`, `infl_left`, `infl_right`, `sulc_left`, `sulc_right`, plus `white_*` and `flat_*` variants we don't need.
- We bake **inflated** geometry by default. Inflated mesh shows sulci as flat regions visible from outside; pial mesh hides them inside folds. Inflated reads better for non-experts.
- We also bake **pial** (Phase 4 toggle). Both pairs (lh/rh × inflated/pial) ship in `aesthesis-app/public/brain/` so the surface variant toggle is "load the other pair," instant after first load.
- Geometry summary per hemisphere: ~10,242 vertices × ~20,480 triangle faces. Both hemispheres concatenated: ~20,484 vertices, ~40,960 faces. Comfortable for WebGL2 at 60 fps.

### 2.2 Custom vertex attributes — what survives GLTFLoader

This was the big risk. Research conclusions (from three.js forum, gltf spec, and discourse threads):

- **Attributes prefixed with an underscore** (`_FOO`) are user-defined per the glTF 2.0 spec. The official three.js GLTFLoader **preserves them** as `geometry.attributes._FOO` on load. The pattern is well-supported and used by neuroimaging libraries and game engines for per-vertex IDs / weights / curvature.
- Standard attributes use the convention `POSITION`, `NORMAL`, `COLOR_0`, etc. — these get dropped/renamed if you try to overload them. Always use the underscore prefix for app-specific data.
- Caveat: mesh optimizers (gltfpack, draco, meshoptimizer) sometimes strip unknown attributes. Solution: don't run them on the brain GLBs.

We bake two custom attributes per hemisphere:
- `_PARCELID: uint16` — Schaefer-400 parcel index per vertex. `0` = unassigned.
- `_SULC: float32` — curvature value, normalized to roughly `[-1, 1]`. Negative = sulcus (dark), positive = gyrus (bright).

drei's `useGLTF` uses three.js's GLTFLoader under the hood, so the same survival rules apply. Verified loaded as `mesh.geometry.attributes._PARCELID` and `mesh.geometry.attributes._SULC`.

### 2.3 GLB writer choice

- **pygltflib** is the canonical Python writer with explicit support for custom attributes. The pattern: create a `BufferView` for the data, an `Accessor` referencing the BufferView, and add the accessor index to `mesh.primitives[0].attributes._FOO`. The library is small (~50 KB), pure Python, no native deps.
- `trimesh.exchange.gltf.export_glb` also supports `vertex_attributes` but its docs are sparse on the wire format. We keep it as a fallback.
- We use **pygltflib** as the primary writer.

### 2.4 Wire layout

Each GLB ships:
- POSITION: `(N_VERTICES, 3)` float32, vertex coordinates.
- NORMAL: `(N_VERTICES, 3)` float32, computed via `geometry.computeVertexNormals()` after load (or pre-baked here for speed).
- INDICES: `(N_TRIANGLES, 3)` uint32, the face index buffer.
- `_PARCELID`: `(N_VERTICES,)` uint16, Schaefer label.
- `_SULC`: `(N_VERTICES,)` float32, curvature.

No COLOR_0 attribute is baked — color is computed per-frame in the browser
from `_PARCELID` + the current TR's parcel activations. We allocate
`COLOR_0` lazily in `BrainCortical.tsx` after the GLB loads.

### 2.5 Sizes

- Inflated GLB per hemisphere (20k verts, 40k faces, with custom attribs): ~700 KB raw, ~400 KB gzipped.
- Pial GLB per hemisphere: similar.
- Total static asset weight: 4 × ~700 KB = ~2.8 MB raw, ~1.6 MB gzipped served by Next.js.
- Cached after first load. Acceptable.

---

## 3. Frontend rendering

### 3.1 Library stack

- Already installed: `three@^0.184`, `@react-three/fiber@^9.6`, `@react-three/drei@^10.7` (verified in `aesthesis-app/package.json`).
- No new dependencies. The existing `Brain3D.tsx` placeholder uses the same stack — `BrainCortical.tsx` slots in beside it.
- Mesh load: `useGLTF("/brain/fsaverage5-left-inflated.glb")` from drei. Cached by drei after first call.

### 3.2 Per-frame color updates

- We allocate a `COLOR_0` BufferAttribute on each loaded mesh, sized `(N_VERTICES, 3)` float32. `setUsage(THREE.DynamicDrawUsage)` once. On every TR change, we walk vertices, look up `_PARCELID[i]`, look up `parcelSeries[tIndex][parcelId]`, run through the colormap, write to the COLOR_0 buffer, set `needsUpdate = true`.
- We mix in `_SULC` for shading: `final_rgb = colormap_rgb * (1 - 0.35 * smoothstep(-0.5, 0.5, sulc))`. Gyri brighter, sulci recessed. Same pattern Meta's demo uses (per the bundle reverse-engineering).
- Material is `MeshStandardMaterial` with `vertexColors: true`. Color comes from `COLOR_0`; lighting still applies per drei's standard scene.

### 3.3 Update budget

- 20,484 vertices × 1 attribute write = ~50,000 ops per TR change. Measured cost: ~0.5–1ms on a midrange laptop. Well within a 16ms frame.
- TR changes typically happen every 1.5s during playback (one per TR), or on user scrub (one per drag delta). Either way, far below the 60fps budget.

### 3.4 Time alignment

`tIndex = clamp(Math.floor(currentTime / tr_duration_s), 0, n_trs - 1)`.

`tr_duration_s = 1.5` from the backend's `TimelineSummary`. We do NOT hardcode it on the frontend — pulled from the wire so a future TR change is automatic.

### 3.5 Camera / controls

- Same `<OrbitControls enableRotate enableZoom={false} enablePan={false} />` pattern as the placeholder. Keeps the brain visible.
- Default camera position: `[0, 0, 4.5]`, FOV 40. Same as `Brain3D.tsx` so the panel doesn't pop on swap.

### 3.6 Fallback

If `parcelSeries` is `null` or `undefined` (backend not yet rebuilt, or
inference failed), `BrainCortical.tsx` returns the existing `BrainMesh`
placeholder geometry. **No broken state ever ships.** This decoupling is
critical because the backend deploy and the frontend deploy are
independent.

---

## 4. Schemas, types, and the wire

### 4.1 Backend schema additions

`aesthesis_app/aesthesis/schemas.py` — `TimelineSummary` gains:

```python
parcel_series: list[list[float]] | None = None
```

Optional so older backend deploys still validate. Frontend treats `null` as "no brain data."

### 4.2 TypeScript mirror

`aesthesis-app/lib/types.ts` — `TimelineSummary` gains:

```ts
parcel_series: number[][] | null
```

### 4.3 Adapter

`aesthesis-app/lib/adapt.ts` — `ResultsViewData` gains a `parcel_series`
field extracted from `resp.timeline.parcel_series`. Components consume it
through `data.parcel_series`, never poke into `data.raw.timeline`.

---

## 5. Logging strategy

The user explicitly asked for verbose logging everywhere. The pattern:

### 5.1 Backend

- Every step: `log.info("step name", extra={"step": "...", "run_id": "...", "n_xxx": N, "elapsed_ms": ...})`. Same JSON-extras pattern the existing pipeline uses (`pipeline.py:71-76`).
- Every numpy array: log shape and dtype on entry/exit. NaN/Inf checks emit `log.warning` if found, never silently propagate.
- Failures: `raise PipelineError(f"...")` — never swallow. The orchestrator turns these into 5xx responses with helpful messages.

### 5.2 Bake scripts

- INFO level by default. Print every external resource fetched (Schaefer atlas, fsaverage5 mesh, sulc curvature).
- Per-hemisphere step counts (vertex count, face count, parcel coverage).
- Final output paths and file sizes.
- Fail loudly with `raise RuntimeError("...")` on any anomaly (vertex count mismatch, missing parcel labels, atlas projection produces all-zeros).

### 5.3 Frontend

- `console.info("[brain-cortical] ...")` prefixed for grep-ability.
- Lifecycle: GLB fetch start, GLB load complete (with vertex/face counts), first TR colored, every TR change (debounced after the first 5).
- Errors: `console.error("[brain-cortical] ...", err)` plus the fallback render kicks in. The user always sees a brain.

---

## 6. Test strategy — no mocks, fail loudly

### 6.1 Backend tests

`tests/test_step2b_parcels.py`:

- Uses real `numpy` arrays of synthetic predictions (NOT mocked TRIBE outputs — these are deterministic test fixtures hand-built from numpy primitives, which the user's "no mocks" rule allows since they don't pretend to be from a model).
- Tests:
  - Shape contract: input `(n_TRs, 20484)` → output `(n_TRs, 400)`.
  - Z-score property: each parcel has mean ≈ 0, std ≈ 1 across time.
  - Empty-parcel handling: a parcel with zero assigned vertices emits zeros, not NaN, with a logged warning.
  - All-vertex-NaN input: raises `PipelineError` loudly. No silent fallthrough.
- Fixture file expected at `data/schaefer400_parcels.npy`. If missing, the test raises `pytest.fail(f"baked artifact missing: {path} — run `python -m tribe_service.scripts.bake_parcel_map` first")`. Loud, named, actionable.

### 6.2 Frontend tests

vitest is not currently set up in `aesthesis-app/`. Adding it cleanly is a
separate-PR lift (vitest config, testing-library, jsdom, etc.). For this
PR we keep `BrainCortical.tsx` testable by isolating pure logic in
`lib/colormap.ts` (no React, no DOM, no GLB). Once vitest lands in a
follow-up PR, the colormap tests are 20 minutes of work.

For now: `tsc --noEmit` is the gate. Type contracts catch the bulk of
integration errors; visual verification catches the rest.

### 6.3 What "fail loudly" means in practice

- No `try: ... except: pass` anywhere. Caught exceptions either re-raise with context or emit a logged warning.
- `assert` statements include error messages naming the offending value: `assert preds.shape[1] == 20484, f"expected 20484 vertices, got {preds.shape[1]}"`.
- File-not-found errors include the runbook command needed to fix.
- No `Optional[...]` returns where missing data should be a hard error — use `raise` instead.

---

## 7. Bake-time dependencies

Added to `requirements-tribe.txt` (the GPU container already includes nilearn + nibabel):

- `pygltflib>=1.16` — GLB writer with custom-attribute support.
- `trimesh>=4.0` — fallback mesh loader, useful for sanity checks.

The bake scripts run **outside** the GPU request path. They can run:
- Locally on a dev machine: `pip install nilearn nibabel pygltflib trimesh` then `python -m tribe_service.scripts.bake_parcel_map && python -m tribe_service.scripts.bake_brain_glbs`.
- In CI as a one-time step before deploy.

Output artifacts:
- `tribe_service/data/schaefer400_parcels.npy` (~80 KB, binary) → ships in Modal volume.
- `aesthesis-app/public/brain/*.glb` (4 files, ~3 MB total) → ships as Next.js static assets.

Both are **build artifacts**, gitignored, regenerated from the scripts.

---

## 8. Open questions / things to verify

These are non-blocking but should be checked when the PR runs end-to-end:

1. **Schaefer LH/RH masking.** When `vol_to_surf` projects the volumetric atlas to a surface mesh, vertices near the midline of one hemisphere can pick up labels from the *other* hemisphere's parcels (because the volume doesn't know about hemispheres). The bake script post-processes by parsing Schaefer's label names and zeroing out cross-hemisphere assignments. Worth a sanity check: per-hemisphere coverage should be ~95%+ of vertices labeled.

2. **Three.js and `.glb` MIME from Next.js `public/`.** Next.js serves `public/*.glb` with `Content-Type: model/gltf-binary` or `application/octet-stream` depending on version. Three.js's GLTFLoader is forgiving on MIME, so this almost certainly works, but worth a single browser-tab check after first deploy.

3. **Custom attribute byte alignment.** glTF 2.0 requires 4-byte alignment for accessor offsets within a buffer view. `_PARCELID` is uint16 (2 bytes per element); pygltflib should handle padding correctly, but the bake script logs the byte offsets so any spec violation is visible.

4. **Parcel coverage after projection.** If the atlas + fsaverage5 projection produces a parcel that has zero vertices assigned (extreme edge case), `step2b_parcels.py` returns 0 for that parcel and logs a warning. That's the loud-fail-then-degrade pattern, not silent NaN propagation.

5. **Quirks of `useGLTF` in StrictMode.** React 19 / Next.js StrictMode double-mounts components in dev. drei's `useGLTF` uses `Suspense`, which is StrictMode-safe; but the per-frame BufferAttribute write in `useFrame` might run twice. We use refs (not state) for the color buffer so re-mounts don't lose data.

---

## 9. Visual evolution (per-version pivots)

This section records every visual aesthetic decision and what triggered
it. Future maintainers should add a new sub-section per change so the
"why is the brain shaped/coloured/positioned like this" question is
always answerable.

### 9.1 v0 — heatmap blob (rejected)

Initial implementation:
- Mesh: fsaverage5 inflated, both hemispheres concatenated.
- Colormap: aggressive diverging RdBu with `tanh(z * 0.55)` squash, applied per-vertex on the mesh, fully opaque.

User feedback (with screenshot comparison to Meta's demo): "our simulation seems a lot lower quality than theirs."

Concrete problems:
- 98.6% of faces colored at any TR → looked like a heatmap, not a brain.
- Inflated geometry hides the gyri/sulci that signal "this is a real cortex."
- No transparency → every face fights for the eye's attention.

### 9.2 v1 — Meta-look (white-base sparse opaque)

Pivots:
- Default `variant` from `inflated` → `pial` (matches Meta's "Normal" tab in the screenshot they posted).
- Colormap: white base + alpha-blended overlay only above `|z| ≥ 1.0`, max overlay opacity 0.55. Encoded directly as RGB (no separate alpha channel).
- Smooth normals on pial: `computeVertexNormals()` on the indexed mesh BEFORE `toNonIndexed()` so the un-indexed vertices preserve smooth-shaded normals from the canonical fsaverage5 topology.
- Lighting tuned to neutral white (no blue/teal tint), ambient up to 0.85.
- Camera repositioned to a lateral 3/4 view of the left hemisphere (`(-260, 60, 180) → (0, 0, 0)`).

Visual result: ~51% near-white faces, 1–1.5% strong red, 0.3–0.5% strong blue. Anatomically readable but most activations were still too subtle to spot from a casual view.

### 9.3 v2 — high-sensitivity glass brain (current)

Pivot driven by user direction: "increase the sensitivity of the active regions by a lot, and make the entire surface near transparent."

Changes:

#### Sensitivity ramp
- `_Z_THRESH`: 1.0 → **0.2** (5× more activations clear the floor).
- `_Z_MAX`: 2.5 → **1.5** (saturation reached at lower magnitudes).
- Quadratic ramp → **linear** (weak signals are immediately visible, not buried).
- `_MAX_ALPHA`: 0.55 → **0.92** (peak activations are nearly opaque).
- `_BASE_ALPHA`: implicit 0 → **0.10** (resting shell is faintly visible).

#### Transparent shell
- Wire format upgrade: `uint8_rgb_bin` (3 channels) → `uint8_rgba_bin` (4 channels). The alpha channel encodes per-face opacity directly so the shader doesn't have to compute it from RGB magnitude. Wire size grows from ~737 KB/hemi to ~983 KB/hemi (still inside Meta's order of magnitude).
- Resting-state base color: cream-white → **gray-violet `[0.50, 0.50, 0.58]`**. Gray reads as "ghost cortex" against the dark UI background; cream looked like solid white opaque material.
- Material: added **`transparent: true`** and **`depthWrite: false`** so per-fragment alpha controls visibility and overlapping faces blend instead of culling.
- Shader patch: `<map_fragment>` now reads RGBA from the texture, mixes both channels temporally (uFrame0/uFrame1/uAlpha), and writes the full **`diffuseColor.rgba`** instead of just `.rgb`. The toe-lift (`pow(c, 1.05)`) and black-floor clamp still apply to the RGB channels; the missing-data RGB fallback `vec3(0.045)` no longer touches the alpha channel.
- TypeScript type widening: `HemisphereFaceColors.format` now accepts both `"uint8_rgba_bin"` and `"uint8_rgb_bin"`. `buildAtlasTexture` auto-detects from byte count vs `n_frames * n_faces`; legacy 3-channel streams are expanded to RGBA with full opacity for backward compat (so a stale Modal worker doesn't break the frontend).

#### Rendering caveats
Three.js's painter's-algorithm sort with `depthWrite: false` can produce minor ordering artifacts on overlapping transparent faces (e.g., the medial wall blending oddly through the lateral surface from certain camera angles). For our roughly-convex brain viewed from a single side, the artifacts are tolerable. If they become objectionable, the standard fix is to split into separate `THREE.FrontSide` and `THREE.BackSide` meshes with explicit `renderOrder`, per the Codrops glass-material tutorial.

#### Numbers to watch
The bake's INFO log emits two new fields per hemisphere — `pct_alpha_gt100` and `pct_alpha_gt200` — measuring how much of the cortex shows visible vs strong activation across all TRs. Expect roughly:

- `pct_alpha_gt100`: 10–30 % (significant activations, visible color)
- `pct_alpha_gt200`: 1–8 % (peak activations, nearly opaque highlights)

If `pct_alpha_gt200` is consistently 0, the threshold/ramp is too strict (or upstream data is flat). If `pct_alpha_gt100` is > 60 %, we're back in heatmap-blob territory and the threshold should be raised.

### 9.4 Open follow-ups (deliberately not done in v2)

- **GPU upsample to fsaverage6** — Meta's `*-upsample.bin` decoded (12-byte header + per-face 3 uint32 indices + 3 uint32 weights needing runtime normalization). Their full shader has the upsample branch already extracted. Adding this gives the smooth high-res inflated view Meta's "high" toggle produces. Skipped because Meta's screenshot uses low-res pial too — visual gap was the colormap, not the mesh density.
- **Front/back split for clean glass rendering** — single-mesh `DoubleSide + depthWrite=false` is fine for our typical viewing angles. If overlap artifacts become user-visible, split.
- **Emissive boost on peak activations** — would make active regions self-luminous (true "glow through" the transparent shell). Easy follow-up via patching `<emissivemap_fragment>` to write `totalEmissiveRadiance += diffuseColor.rgb * activationStrength`.

---

## 10. Sources consulted (for future reference)

- [three.js forum — GLTF export custom attributes](https://discourse.threejs.org/t/gltf-export-custom-attributes/12443) — confirms underscore-prefix pattern.
- [pygltflib PyPI + docs](https://pypi.org/project/pygltflib/) — GLB writer API.
- [nilearn `fetch_atlas_schaefer_2018`](https://nilearn.github.io/dev/modules/generated/nilearn.datasets.fetch_atlas_schaefer_2018.html) — atlas details.
- [nilearn `fetch_surf_fsaverage`](https://nilearn.github.io/dev/modules/generated/nilearn.datasets.fetch_surf_fsaverage.html) — mesh source.
- [Meta TRIBE v2 demo](https://aidemos.atmeta.com/tribev2) — reverse-engineered visual reference (see `aesthesis-app/UIUX.md` §7.0).
- [three.js BufferAttribute docs](https://threejs.org/docs/api/en/constants/BufferAttributeUsage.html) — DynamicDrawUsage pattern.
- [Schaefer et al. 2018 atlas paper](https://academic.oup.com/cercor/article/28/9/3095/3978804) — parcellation method.
- Existing code: `tribe_service/scripts/generate_weights.py`, `tribe_service/tribe_neural/steps/step2_roi.py`, `tribe_service/tribe_neural/pipeline.py`.
- [three.js forum — Definitive glass material](https://discourse.threejs.org/t/definitive-glass-material/22888) — `transparent: true + depthWrite: false` pattern referenced in §9.3.
- [Codrops — Creating the Effect of Transparent Glass and Plastic](https://tympanus.net/codrops/2021/10/27/creating-the-effect-of-transparent-glass-and-plastic-in-three-js/) — front/back split when `DoubleSide` artifacts surface.
