# Aesthesis — UI/UX Implementation Plan

**Scope.** This is the implementation reference for `aesthesis-app/` — the Next.js
frontend. It tells you exactly what to build, in what order, with which files,
which props, which states. Architectural rationale lives in `DESIGN.md` at the
repo root; this doc is the operating manual for the UI.

**Audience.** A frontend engineer who knows React/TypeScript but is new to the
codebase. By the time you finish reading this, you should be able to build any
page state, hook any new data field through, and ship a feature without asking
where things live.

**Status.** Most of the foundation is in place. The remaining work is the real
brain visualization (Phase 5 below) and the polish/a11y pass (§9, §10).

---

## 1. Goals & Non-Goals

### Goals

- Make a 30-second video upload feel like a brain scan that produces a real
  reading. The product narrative is "neural precision" — every UI decision
  either reinforces that or undercuts it.
- Tight feedback loop between the video timeline, the brain state, and the
  insight cards. Hovering, clicking, scrubbing should all stay in sync.
- Calm, dark, instrument-panel aesthetic. Not a SaaS landing page. Not a
  dashboard. Closer to a piece of lab equipment.
- Zero "AI slop" patterns: no purple-gradient hero, no 3-column feature grid
  with icons-in-circles, no centered-everything layout. (See §3.4.)

### Non-Goals (for now)

- Mobile. The app is desktop-first; mobile gets a graceful "open this on
  desktop" message until we have a real mobile design.
- Multi-video A/B comparison. The pivot to single-video is final
  (`DESIGN.md §17`).
- Account / auth / persistence. Every analysis is one-shot and lives only in
  in-memory React state until the page reloads.

---

## 2. Status Snapshot

### What's already in place

| Area | State | Notes |
|---|---|---|
| Page-state machine | ✅ Working | `app/page.tsx` switches on `AppState` (`"landing" \| "capture" \| "assess" \| "analyzing" \| "results"`) |
| Backend client | ✅ Working | `lib/api.ts` — single `analyze()` POST, full error mapping, run-id propagation |
| Wire ↔ view adapter | ✅ Working | `lib/adapt.ts` produces `ResultsViewData` from `AnalyzeResponse` |
| Type parity with backend | ✅ Working | `lib/types.ts` mirrors `aesthesis_app/aesthesis/schemas.py` |
| Landing page | ✅ Working | `components/Landing.tsx` |
| Capture / Assess flows | ✅ Working | `components/CaptureView.tsx`, `components/AssessView.tsx`, `components/UploadZone.tsx` |
| Analyzing state | ✅ Working | `components/AnalyzingView.tsx`, `components/LiveStreamPanel.tsx` |
| Results layout | ✅ Working | `components/ResultsView.tsx` — video + brain + chart + insights + assessment |
| Brain placeholder | ⚠️ Generic icosahedron | `components/Brain3D.tsx` — must be replaced by Phase 5 |
| Insight time bounds | ✅ Fixed | Backend clamps `timestamp_range_s` to `[0, duration_s]` (`synthesizer.py`) |
| Video panel sizing | ✅ Fixed | `VideoPlayer.tsx` capped at `50vw × 50vh` |
| Brain panel sizing | ✅ Fixed | `ResultsView.tsx` — paired square panel at `50vh` |
| Brain rotation | ✅ Fixed | `OrbitControls enableRotate` |
| Brain colormap | ⚠️ Sign-aware but synthetic | `Brain3D.tsx` — placeholder until real cortical mesh lands |

### Recent fixes shipped (since last review)

1. **Insight timestamps clamped to video length.** `aesthesis_app/aesthesis/synthesizer.py` now passes `duration_s` into both Gemini prompts and runs every returned insight through `_clamp_insight_range()`. Insights whose start lies past the video end are dropped; ranges that overshoot the end are clipped. No card can point past the video again.
2. **Video player sized.** `VideoPlayer.tsx` is `flex-1` with `maxWidth: 50vw, height: 50vh`; the inner aspect-ratio `flex-1` was the duelling-flex trap that made the frame fill the viewport.
3. **Brain panel paired with video.** `ResultsView.tsx` now wraps `Brain3D` in a square panel sized at `50vh × 50vh` with a header (`Neural state · t = X.Xs`) matching the video header. Panels sit side-by-side at equal height.
4. **Brain color sign-aware.** `Brain3D.tsx#computeTargetColor` squashes z-scored ROIs through `tanh(z * 0.7)` and applies asymmetric warm/cool shifts. Negative z no longer collapses into "dim."
5. **OrbitControls live.** Rotate enabled; zoom/pan stay off so the brain can't be lost off-screen.
6. **Suspense fallback.** Generic spinner replaced with a soft pulsing orb that hints at the brain coming in (no layout pop on lazy resolve).

### What's left

- **Real cortical brain.** Bake 4 fsaverage5 GLBs (left/right × inflated/pial), emit per-parcel activity from TRIBE, render via `@react-three/fiber` (already installed) with a per-vertex colormap driven by `parcel_series`. See §7.
- **Polish pass.** A11y, responsive, empty/error states, performance budget. See §9–§13.
- **Sync between insight hover and brain regions.** Highlight cited parcels when an insight card is hovered. Phase 5b.

---

## 3. Design System

### 3.1 Color tokens

Defined in `app/globals.css`. Treat as the single source of truth — never
hardcode hex values in components.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0B0F14` | Page background |
| `--panel` | `rgba(255,255,255,0.04)` | Panel surface (`.panel` class) |
| `--border` | `rgba(255,255,255,0.08)` | Panel borders, dividers |
| `--primary` | `#7C9CFF` | Primary actions, links, video-panel accent |
| `--accent` | `#5CF2C5` | Status/success, brain-panel accent |
| `--danger` | `#FF6B6B` | Errors, friction signals |
| `--text-muted` | `rgba(255,255,255,0.45)` | Secondary copy |

**Body text:** `#e8eaf0` (set on `body` directly).
**Tertiary text:** `rgba(255,255,255,0.30)` for hover hints, captions, "drag to rotate" labels.

**Per-ROI palette** (`lib/types.ts:146`) is the canonical mapping for chart
lines, brain region tints when an ROI is cited, and any future ROI-specific UI.
Keys must match `ROIKey` exactly.

| ROI | Color |
|---|---|
| `aesthetic_appeal` | `#A78BFA` |
| `visual_fluency` | `#38BDF8` |
| `cognitive_load` | `#7C9CFF` |
| `trust_affinity` | `#34D399` |
| `reward_anticipation` | `#5CF2C5` |
| `motor_readiness` | `#FBBF24` |
| `surprise_novelty` | `#F472B6` |
| `friction_anxiety` | `#FF6B6B` |

### 3.2 Typography

Defined via Next.js font setup in `app/layout.tsx` (Geist Sans + Geist Mono).

| Use | Class | Weight | Notes |
|---|---|---|---|
| Page hero title | `text-5xl font-light` | 300 | Landing only |
| Section heading | `text-sm font-medium` | 500 | Panel headers |
| Body | default | 400 | `#e8eaf0`, ~14–16px |
| Caption / muted | `text-xs` | 400 | `rgba(255,255,255,0.35)` |
| Tiny label | `text-[10px] tracking-wide` | 400 | "drag to rotate" style |
| Mono numeric | `font-mono` | 400 | Timestamps, run IDs, metrics |

**Rules.**
- Never use `font-bold`. The dark theme handles emphasis through color and size, not weight.
- Mono only for fixed-width data (timestamps, IDs, numerics). Never for prose.
- No default font stacks (`Inter`, `Roboto`, `Arial`). `Geist Sans` is the brand voice.
- No emoji as design elements.

### 3.3 Spacing

Tailwind defaults, but with these conventions:

| Use | Spacing |
|---|---|
| Page horizontal padding | `px-8` (32px) |
| Page vertical padding | `py-8` (32px) |
| Section gap | `gap-8` (32px) |
| Within-panel padding | `p-5` (20px) for content panels, `px-4 py-3` (16/12px) for headers |
| Element gap (rows) | `gap-3` (12px) for tight, `gap-5` (20px) for standard |
| Border radius | `rounded-xl` (12px) for panels, `rounded-lg` (8px) for buttons, `rounded-2xl` (16px) for hero panels (insights, assessment) |

Container max-width is `max-w-6xl` (1152px), centered. Anything that needs to
break out (full-bleed hero, edge-to-edge timeline) does so explicitly with
`-mx-8` or a flag.

### 3.4 Motion

- Page transitions: `framer-motion` `initial={{ opacity: 0, y: 20 }}`, `animate={{ opacity: 1, y: 0 }}`, stagger via `transition={{ delay: 0.1 * i }}`.
- Button hover: scale `1.005`, color/border easing 150ms.
- Brain rotation: idle drift (`rotation.y += 0.004` per frame, ~2 RPM).
- Live stream pulse: 2-second ease-in-out opacity cycle.
- **Never** use bouncy easings. Easings are `ease`, `easeInOut`, `linear`. No `spring(50, 5)` chaos.
- **Never** auto-rotate the brain. The user controls the rotation.

### 3.5 Depth (panels, glow, blur)

- Standard panel: `.panel` class — `rgba(255,255,255,0.04)` background, `1px` border at `rgba(255,255,255,0.08)`, `backdrop-filter: blur(16px)`. Defined in `app/globals.css:62`.
- Primary glow (CTAs, active live frames): `.glow-primary` — `0 0 24px rgba(124,156,255,0.18), 0 0 80px rgba(124,156,255,0.06)`.
- Accent glow (brain card on activity spike): `.glow-accent` — same with the accent color.
- Hard rule: no decorative drop shadows on cards. Glows are reserved for *signal*.

### 3.6 AI-slop blacklist

These patterns are banned regardless of how easy they'd be:

- Purple/violet gradient backgrounds.
- 3-column feature grids with icons in colored circles.
- Centered-everything (`text-align: center` on every heading/paragraph).
- Uniform large `border-radius` on every element.
- Decorative blobs, floating circles, wavy SVG dividers as section fillers.
- Emoji in headings or as bullet glyphs.
- Colored-left-border cards (`border-left: 3px solid <accent>`).
- Generic hero copy ("Welcome to…", "Unlock the power of…", "Your all-in-one solution for…").
- Cookie-cutter section rhythm (hero → 3 features → testimonials → pricing → CTA).

If a design starts to feel like one of these, restart the section.

---

## 4. Layout System

### 4.1 Page shell

```
+------------------------------------------------+
| Top nav (h: 64px, border-bottom)               |
+------------------------------------------------+
|                                                |
|  Content max-w-6xl mx-auto px-8 py-8           |
|  - Section 1                                   |
|  - Section 2                                   |
|  - ...                                         |
|                                                |
+------------------------------------------------+
```

The shell is implicit per-state. There is no global layout component beyond
`app/layout.tsx` (which only sets fonts + globals). Each state (`Landing`,
`CaptureView`, `AnalyzingView`, `ResultsView`) renders its own full-screen tree
and swaps via `app/page.tsx`.

### 4.2 The panel pattern

The fundamental unit is a `.panel` div with an optional header strip and a
content area. Used by VideoPlayer, the brain card, the insight grid, the
assessment panel, and the chart.

```tsx
<div className="rounded-xl overflow-hidden panel flex flex-col">
  {/* Optional header */}
  <div
    className="flex items-center gap-2 px-4 py-3 shrink-0"
    style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
  >
    <div className="w-2 h-2 rounded-full" style={{ background: ACCENT }} />
    <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>
      Section title
    </span>
    {/* Optional trailing meta */}
    <span className="ml-auto text-xs font-mono" style={{ color: "rgba(255,255,255,0.35)" }}>
      meta
    </span>
  </div>

  {/* Content */}
  <div className="relative flex-1 min-h-0 ...">
    ...
  </div>
</div>
```

Variants:
- **Hero panel** (insights grid, assessment): `rounded-2xl p-5 flex flex-col gap-4`, no header strip.
- **Square panel** (brain card): outer is `width = height` from `style`, inner uses `flex flex-col` with `flex-1 min-h-0` content.
- **Bleed panel** (video): same shell, but the content area is `bg-black aspect-video` or bounded by `height: 50vh`.

### 4.3 Viewport-aware sizing rules

`max-w-6xl` (1152px) is the row width. Inside it:

| Element | Constraint | Why |
|---|---|---|
| Video player | `flex-1 min-w-0`, `maxWidth: 50vw`, `height: 50vh` | Caps both dimensions; never bigger than half the viewport |
| Brain card | `width: 50vh, height: 50vh` (square) | Squared off the video's height; balances the row |
| Chart | full row width | Timeline benefits from horizontal space |
| Insights grid | `col-span-2` of a 3-col grid | 2 cards per row inside the 2-col area |
| Assessment | `col-span-1` of the same grid | Sits beside the insights |

`flex-1` on a panel means "share row space." `min-w-0` is mandatory on flex
children that contain text or iframes — without it, long content breaks the
flex math and overflows.

**Never** use `aspect-video` and `flex-1` on the same element. They duel; the
result is browser-dependent. Pick one (we picked explicit heights for the
video panel; see the comment in `VideoPlayer.tsx`).

### 4.4 Grid for results bottom row

`ResultsView.tsx` uses a 3-column grid for the bottom section:

```tsx
<motion.section className="grid grid-cols-3 gap-6">
  <div className="col-span-2 panel rounded-2xl p-5 ...">
    {/* Insights — 2 cols */}
  </div>
  <div>
    <OverallAssessmentPanel ... />  {/* 1 col */}
  </div>
</motion.section>
```

Insight cards are themselves a 2-col `grid grid-cols-2 gap-3` inside the
`col-span-2` host. Net: 2 insight cards per row, with the assessment panel
to the right at 33% width.

---

## 5. Page States & Flows

The app is a state machine driven by `app/page.tsx`. There are 5 states; only
one is rendered at a time.

```
landing ──► capture ──► analyzing ──► results
   │           │                          │
   └────────► assess ─────────────────────┘
   ▲                                      │
   └── reset() ───────────────────────────┘
```

### 5.1 `landing`

- Component: `Landing.tsx`
- Trigger to leave: `onCaptureAndAssess` (full pipeline) or `onSkipToAssess` (upload-only).
- Reads: nothing.
- Writes: `appState`.
- Ambient drift blobs are decorative; respect the layout, never block content.

### 5.2 `capture`

- Component: `CaptureView.tsx`
- User enters URL + goal. Optional: live agent capture.
- Trigger to leave: file picked + "Analyze" clicked → `appState = "analyzing"`.

### 5.3 `assess`

- Component: `AssessView.tsx`
- Skip-the-capture upload flow. Drop a recorded MP4 directly.
- Same exit as `capture`.

### 5.4 `analyzing`

- Component: `AnalyzingView.tsx`
- Driven by the in-flight `analyze()` Promise. `app/page.tsx:30` cancels via
  `AbortController` if the user navigates away.
- States within state:
  - **Pre-stream:** "Validating video…"
  - **Streaming:** `LiveStreamPanel` shows backend frames if WS is up.
  - **Synthesizing:** "TRIBE encoding…" → "Generating insights…".
  - **Done:** transitions to `results` once `analyze()` resolves.
- Errors mapped from `AnalyzeError` show under the panel, with a "Try again" button.

### 5.5 `results`

- Component: `ResultsView.tsx`
- Reads: `ResultsViewData` (`lib/adapt.ts`).
- Hosts the four content sections in this order:
  1. **Top row:** `VideoPlayer` (left) + brain card (right).
  2. **Middle row:** `BrainChart` — full-width timeline of all 8 ROIs.
  3. **Bottom row:** Insights grid (`col-span-2`) + `OverallAssessmentPanel`.
- Local state: `currentTime`, `currentROI` (derived).

---

## 6. Component Inventory

For each, the source file, props, and the single sentence that justifies its
existence. Keep this list current — when a new component lands, append a row.

| Component | File | Purpose |
|---|---|---|
| `Landing` | `Landing.tsx` | Marketing copy + two CTAs into the pipeline |
| `CaptureView` | `CaptureView.tsx` | URL + goal input + agent-capture branch |
| `AssessView` | `AssessView.tsx` | Direct MP4 upload, no capture |
| `UploadZone` | `UploadZone.tsx` | Drop target shared by Capture and Assess |
| `AnalyzingView` | `AnalyzingView.tsx` | Long-poll progress while `/api/analyze` runs |
| `LiveStreamPanel` | `LiveStreamPanel.tsx` | WS-driven preview of backend frames during analysis |
| `ResultsView` | `ResultsView.tsx` | The orchestrator panel for the post-analysis page |
| `VideoPlayer` | `VideoPlayer.tsx` | The user's MP4, controlled by `currentTime`, capped at `50vw × 50vh` |
| `Brain3D` | `Brain3D.tsx` | 3D neural state visualizer — placeholder geometry today, real cortical mesh per §7 |
| `BrainChart` | `BrainChart.tsx` | Multi-line timeline of 8 ROIs over the full video, click-to-seek |
| `InsightCard` | `InsightCard.tsx` | One Gemini-generated insight, click-to-seek to its `timestamp_range_s[0]` |
| `OverallAssessmentPanel` | `OverallAssessmentPanel.tsx` | Summary paragraph + strengths + concerns + decisive moment |

### Adding a new component

1. Drop the file in `components/`.
2. If it consumes wire data, route it through `lib/types.ts` types — do not
   shape data inline from `AnalyzeResponse`.
3. If it consumes ROI values, take `ROIValues` (an object) not `number[]` (an
   array) — order should not be implicit.
4. Add a row to the table above.

---

## 7. Brain Visualization Plan (Phase 0 → Phase 5)

The current `Brain3D.tsx` is an icosahedron with sine-based displacement —
shaped like a green dodecahedron. The product story is brain reading; the UI
needs to render the actual cortical surface with TRIBE v2 activity painted on
it. This section is the build-out from placeholder to real.

### 7.0 Reference: how Meta does it

Meta's [TRIBE v2 demo](https://aidemos.atmeta.com/tribev2) was reverse-engineered
from its production bundle. Confirmed stack:

| Layer | Meta's choice | Evidence |
|---|---|---|
| Renderer | **Hand-rolled three.js** (no niivue, no @react-three) | `WebGLRenderer`, `BufferGeometry`, `MeshStandardMaterial`, `GLTFLoader`, `OrbitControls` strings in `BrainViewer-15466291.js` |
| Mesh format | **GLB** (glTF binary) | 8 brain GLBs + 1 head GLB |
| Coloring | **Per-face custom shader** | `faceShaderInstalled`, `faceUniforms`, `faceDirection`, `faceSize`, `faceIndex` uniforms |
| Resolution tiers | **Two GLBs per hemisphere** (low + high) plus an upsample map | `brain-{l,r}-hemisphere{,-high}.glb` + `{left,right}-hemisphere-upsample.bin` |
| Surface variants | **Normal vs Inflated toggle** | `options: [{value:"normal"...}, {value:"inflated"...}]` |
| Animation | **Pre-baked per-face color stream**, fetched as a zip per clip | `face-colors.zip` (~2.9 MB / clip), `fetchBrainColors`, 50× `inflate` (decompress) |

We borrow most of this. Two deliberate departures:

1. **We use `@react-three/fiber` instead of raw three.js.** It's already in our
   stack (`Brain3D.tsx`), gives us declarative scenes, and the low-level
   shader hooks we'd ever need are still available via `useFrame` +
   `BufferAttribute`. We can drop down to raw three.js later if a custom
   shader (Phase 5) demands it.
2. **We compute colors client-side, not server-bake-and-zip.** Meta is
   replaying 50 fixed demo clips on slow client devices — pre-baking is the
   right call. We have one interactive analysis session per user, ~32 KB of
   parcel data per clip, and the brain has to recolor in real time when an
   insight is hovered. Computing in-browser saves 2.9 MB on the wire and
   keeps interactivity cheap.

Everything else — GLB meshes, per-hemisphere split, inflated/normal toggle,
two-tier resolution — we lift directly from Meta's pattern.

### Phase 0 — Decisions (resolved)

| Decision | Choice | Why |
|---|---|---|
| **Visualization library** | `@react-three/fiber` (already installed) | Same family as Meta's three.js. Declarative React API saves boilerplate. No new dependency. |
| **Activity resolution** | Schaefer-400 parcels (Phase 1–4); per-face shader (Phase 5 polish) | 32 KB wire, recolors instantly on hover. Per-face is Meta-quality and comes later. |
| **Mesh source** | fsaverage5 inflated, both hemispheres as separate GLBs | Inflated reads better than pial. Two GLBs lets us toggle visibility (e.g. show only left when an insight cites a left-lateralized region). |
| **Camera** | drei's `OrbitControls`, rotate only | Already in `Brain3D.tsx`. Pan/zoom would let users lose the brain. |
| **Surface variant** | Inflated by default; Normal/Inflated toggle is Phase 4 | Bake both meshes in Phase 2 so the toggle is "load the other GLB" not "rebuild." |
| **Animation** | Client-side parcel→vertex mapping + per-frame color buffer write | Cheap enough for our scale; avoids the 2.9 MB pre-bake step entirely. |

### Phase 1 — Backend: emit per-parcel activity

**Where:** `tribe_service/tribe_neural/` and `aesthesis_app/aesthesis/`

1. **Bake the parcel→vertex map** (one-time build script):
   - File: `tribe_service/scripts/bake_parcel_map.py`
   - Logic: load Schaefer-400 atlas (already pulled by `generate_weights.py:88`), project to fsaverage5, save as `data/schaefer400_parcels.npy` shape `(20484,)` with values `0..399`.
   - Ships in the Modal volume next to `data/masks/` and `data/neurosynth_weights.npz`.

2. **Add a parcel-reduction step** parallel to `step2_roi.py`:
   - File: `tribe_service/tribe_neural/steps/step2b_parcels.py`
   - Input: `preds: (n_TRs, 20484)` + parcel map.
   - Output: `parcel_series: (n_TRs, 400)` — per-parcel mean activation, z-scored per parcel across time.

3. **Wire into pipeline:**
   - `tribe_service/tribe_neural/pipeline.py` — call `step2b_parcels` after `step2_roi`.
   - Add `parcel_series` to the returned `payload`.

4. **App backend:**
   - `aesthesis_app/aesthesis/schemas.py` — add `parcel_series: list[list[float]] | None` to `TimelineSummary`.
   - `aesthesis_app/aesthesis/output_builder.py` — pass through.
   - **Optional gracefulness:** if the TRIBE service hasn't been rebuilt yet (no parcel map on disk), `step2b_parcels` returns `None` and the frontend falls back to the placeholder. Keeps deploys decoupled.

5. **Frontend type mirror:**
   - `aesthesis-app/lib/types.ts` — add the field on `TimelineSummary`.
   - `aesthesis-app/lib/adapt.ts` — pass `parcel_series` into `ResultsViewData` so `ResultsView` doesn't have to dig into `.raw.timeline`.

**Wire size budget:** 30s × 20 TRs × 400 floats × 4 bytes = ~32 KB. No
optimization needed.

### Phase 2 — Bake the brain meshes (4 GLBs)

**Where:** new script + `aesthesis-app/public/brain/`

1. Script: `tribe_service/scripts/bake_brain_glbs.py`
2. Steps:
   - `nilearn.datasets.fetch_surf_fsaverage(mesh="fsaverage5")` — pulls all GIFTI files.
   - For each `(hemi, variant)` in `{(left, inflated), (right, inflated), (left, pial), (right, pial)}`:
     - Load `{infl|pial}_{left|right}.gii`.
     - Add per-vertex attributes:
       - `parcelId: uint16` — Schaefer index (from Phase 1.1's `schaefer400_parcels.npy`, sliced for this hemisphere).
       - `sulc: float32` — curvature from `sulc_{left|right}.gii`, normalized to `[-1, 1]`. Drives the gyrus/sulcus shading band.
     - Write to `aesthesis-app/public/brain/fsaverage5-{left|right}-{inflated|pial}.glb` via `pygltflib` or `trimesh`.
3. Output: 4 files, ~500 KB–1 MB each. Total ~3 MB delivered to the client (cached, served gzipped from Next.js's `public/`).
4. **Build artifact:** add `aesthesis-app/public/brain/*.glb` to `.gitignore`. Document the one-line bake command in `tribe_service/README.md`. The script runs locally or in CI, not on the request path.

**Why per-hemisphere, not concatenated:** Meta does this. It lets the renderer
toggle visibility, fade one hemisphere on a unilateral cited region, and
swap normal↔inflated independently. Concatenating would force us to rebake
on every variant switch.

### Phase 3 — Frontend: real cortical brain

**Where:** `aesthesis-app/components/`

1. **No new dependencies** — we already have `@react-three/fiber`, `@react-three/drei`, and `three`. All four GLBs ship as static assets under `public/brain/`.

2. New component: `components/BrainCortical.tsx`. Replaces the inner mesh of `Brain3D.tsx`; the outer `<Canvas>` + lights stay reusable.
   - **Props:** `{ parcelSeries?: number[][]; tIndex?: number; variant?: "inflated" \| "pial"; highlightedROIs?: ROIKey[] }`.
   - **Mesh load:** `useGLTF("/brain/fsaverage5-left-inflated.glb")` and the right counterpart. Cached by drei's GLTF loader after first request.
   - **Per-frame coloring:** in a `useFrame` callback, for each hemisphere mesh:
     - Read the current TR's parcel activations: `tr = parcelSeries[tIndex]` (length 400).
     - For each vertex `i`, look up `parcelId` (baked attribute) and read `tr[parcelId[i]]`.
     - Convert through diverging colormap (`coolwarm(z)`) → RGB.
     - Mix with the `sulc` attribute (also baked) for sulcal shading: `final = colormap_rgb * (1 - 0.35 * smoothstep(-0.5, 0.5, sulc))`. Gyri brighter, sulci recessed.
     - Write into the mesh's `color` `BufferAttribute` and set `needsUpdate = true`.
   - **Highlighting:** when `highlightedROIs` is non-empty, multiply non-highlighted parcels by `0.4` (dim) and pulse highlighted parcels with a tiny scalar (`0.85 + 0.15 * sin(t * 4)`) before colormap. Fades in 200ms when the prop transitions to/from empty.
   - **Fallback:** if `parcelSeries` is null/undefined (backend not yet updated), render the existing `BrainMesh` placeholder. This keeps the UI valid through staged deploys.

3. **Colormap utility:** `lib/colormap.ts`. ~30 lines. Either:
   - Hand-rolled diverging map (cool blue → white → warm red, anchored at z=0), or
   - `import { interpolateRdBu } from "d3-scale-chromatic"` (already small, ~2 KB).
   - Pick d3 if there's any chance we want to swap colormaps later (viridis, plasma, custom branded).

4. **Integration in `ResultsView.tsx`:**
   - Replace `<Brain3D roiValues={currentROI} />` with `<BrainCortical parcelSeries={data.parcel_series} tIndex={trIndex} highlightedROIs={hoveredROIs} />`.
   - `trIndex = clamp(Math.floor(currentTime / data.raw.timeline.tr_duration_s), 0, parcelSeries.length - 1)`.
   - `hoveredROIs` is already lifted state for the hover-sync feature (§8.3).

5. Once `BrainCortical` is stable, the `BrainMesh` component inside `Brain3D.tsx` becomes the fallback path only. Keep it; delete the export of the placeholder once we trust the new path in production.

### Phase 4 — Polish

- **Surface variant toggle.** Two small chips at the top-right of the brain panel: `[Inflated | Pial]`. Maps to `BrainCortical`'s `variant` prop, which swaps which pair of GLBs is loaded. drei caches both, so the swap is instant after first load.
- **Camera presets.** Three small chips at the bottom of the brain card: `[Lateral L | Lateral R | Top]`. Each animates `OrbitControls` to a preset orbit (azimuth/elevation pair) over ~400ms via a small lerp in `useFrame`.
- **Hover readout.** On vertex hover (raycasting via `@react-three/fiber`'s built-in `onPointerMove`), look up the parcel's dominant ROI from a static `parcel→ROI` map (computed once during the Phase 1 bake). Show a small tooltip: `"Default Mode · aesthetic_appeal"`.
- **Insight ↔ brain link.** When an `InsightCard` is hovered, lift `setHoveredROIs(insight.cited_brain_features)` in `ResultsView`. The brain receives the new prop, dims unrelated parcels, pulses cited ones. On `mouseleave`, clear.
- **TR scrub.** Already handled by the `currentTime → tIndex` derivation — verify in the profiler that re-coloring 20k vertices in `useFrame` doesn't drop below 60 fps. If it does, color update goes to `requestIdleCallback` and runs at 30 fps.

### Phase 5 — Meta-quality polish

Once Phase 4 ships, these are the upgrades that close the gap with Meta's demo:

- **Per-face shader (custom GLSL).** Replace per-vertex colors with a fragment shader that samples a `dataTexture` indexed by `gl_PrimitiveID`. Sharper boundaries between parcels, no smoothing across triangles. Drops down to raw three.js inside a `<primitive>` for shader access.
- **High-res mesh tier + upsample map.**
  - Bake `fsaverage6` (40k vertices) GLBs in addition to fsaverage5 (20k).
  - Bake `aesthesis-app/public/brain/upsample-{left,right}.bin` — a binary `uint16[n_high_vertices]` mapping each high-res vertex to its nearest fsaverage5 vertex.
  - Renderer reads the .bin once, uses it inside the shader to look up colors. Same TRIBE-resolution data, prettier mesh.
- **Pre-baked color stream (Meta-style).** If we ever ship a batch-of-clips comparison view (multiple analyses side-by-side), bake colors server-side and ship a zip per clip. Pattern is Meta's exactly: `face-colors.zip` containing a flat `Float32Array(n_TRs × n_faces × 3)` with metadata.json.
- **Realistic head shell.** Bake `head.glb` (a low-res skull / scalp mesh) and render at low opacity around the brain. Pure aesthetic; sells the "brain reading" story instantly.

### Risks

| Risk | Mitigation |
|---|---|
| `useGLTF` doesn't preserve custom vertex attributes (`parcelId`, `sulc`) through the GLTF loader | 30-min spike: bake a tiny test GLB with `parcelId`, log `mesh.geometry.attributes` after load. If stripped, store the per-vertex `parcelId` array as a separate `.bin` file alongside the GLB and load it in parallel. |
| Modal image bloat from atlas + parcel map | Tiny — Schaefer-400 lookup is ~40 KB. Same volume mount as `data/masks/`. |
| TR alignment off | TRIBE's t-shift is already applied (`tribe_service/tribe_neural/constants.py:18-22`). `t_s = i * TR_DURATION` aligns to stimulus time directly. |
| 60 fps cost on per-frame recoloring | 20k vertices × 1 attribute write is well within budget. If it ever isn't, batch updates to 30 fps via `requestIdleCallback`. |
| GLB asset sizes blow up Next.js page weight | Worst case 4 × 1 MB = 4 MB total, gzipped to ~2 MB. Cached after first load. Acceptable. Could `defer` the brain bundle if FCP suffers (it won't). |
| Colormap accessibility (red/green confusion) | Use a diverging blue↔red map (`coolwarm` / `RdBu_r`), not red↔green. Already the plan. |

### Effort estimate

| Phase | Time |
|---|---|
| 0. Decisions | resolved (see table above) |
| 1. Backend parcel series | 4 hr |
| 2. GLB bake (×4) | 4 hr |
| 3. `BrainCortical.tsx` swap | 4 hr |
| 4. Polish (toggle, presets, hover, insight link) | 4 hr |
| **Real brain on screen, polished** | **~2 days** |
| 5a. Per-face shader | 4 hr |
| 5b. High-res tier + upsample | 4 hr |
| 5c. Head shell | 1 hr |
| 5d. Pre-baked color stream | 4 hr |

### 7.6 First-PR scope (the "demo-able" minimum)

Don't try to ship Phases 1–4 in one PR. Slice to the first thing that
visibly proves the pipeline. Concrete acceptance criteria:

**PR #1 — "Real brain rotates and changes color with the video."**

Files created:
- `tribe_service/scripts/bake_parcel_map.py`
- `tribe_service/tribe_neural/steps/step2b_parcels.py`
- `tribe_service/scripts/bake_brain_glbs.py` (only the inflated pair to start; pial in PR #2)
- `aesthesis-app/public/brain/fsaverage5-left-inflated.glb` (build artifact)
- `aesthesis-app/public/brain/fsaverage5-right-inflated.glb`
- `aesthesis-app/lib/colormap.ts`
- `aesthesis-app/components/BrainCortical.tsx`

Files modified:
- `tribe_service/tribe_neural/pipeline.py` (call `step2b_parcels`)
- `tribe_service/tribe_neural/init_resources.py` (load parcel map)
- `aesthesis_app/aesthesis/schemas.py` (`parcel_series` field)
- `aesthesis_app/aesthesis/output_builder.py` (pass through)
- `aesthesis-app/lib/types.ts` (mirror)
- `aesthesis-app/lib/adapt.ts` (extract)
- `aesthesis-app/components/ResultsView.tsx` (swap component)

Acceptance:
1. Backend emits `parcel_series` of shape `(n_TRs, 400)` for any 30s clip.
2. The brain panel renders a real fsaverage5 inflated mesh (both hemispheres).
3. Drag-to-rotate works (drei `OrbitControls`).
4. Scrubbing the video (or clicking the chart) updates the per-parcel coloring within one frame.
5. If `parcel_series` is missing (backend not yet rebuilt), the placeholder `BrainMesh` renders instead — no broken state.
6. `tsc --noEmit` clean. Bundle size delta < 50 KB (no new deps).

**Out of scope for PR #1:**
- Pial variant + Normal/Inflated toggle (PR #2)
- Camera presets (PR #2)
- Hover tooltips (PR #2)
- Insight ↔ brain link (PR #3)
- Per-face shader (Phase 5, separate PR)
- High-res mesh tier (Phase 5, separate PR)

### 7.7 Spike checklist (do these in the first hour of PR #1)

Before writing the parcel-emission code, run through:

- [ ] `python -c "from nilearn import datasets; print(datasets.fetch_surf_fsaverage(mesh='fsaverage5'))"` → verify the cache pulls and prints all four GIFTI paths.
- [ ] `python -c "from nilearn import datasets; a = datasets.fetch_atlas_schaefer_2018(n_rois=400); print(a['maps'])"` → verify the Schaefer atlas is reachable.
- [ ] `npm install -D pygltflib trimesh` is NOT a thing — these are Python deps. Install via `pip install pygltflib trimesh` in the `tribe_service` venv.
- [ ] `useGLTF` round-trip test: bake a tiny GLB with a custom `parcelId` attribute, load via drei's `useGLTF` in a scratch component, log `mesh.geometry.attributes`. Confirm `parcelId` survives the loader. If not, plan to ship the attribute as a parallel `.bin` (still cheap).
- [ ] Confirm Next.js (this version) serves `.glb` from `public/brain/` with `Content-Type: model/gltf-binary` (or equivalent — three.js's GLTFLoader is forgiving on MIME, but worth checking). If MIME is wrong, add an `app/api/brain/[...path]/route.ts` that streams from `public/` with the correct header.

---

## 8. Interaction Patterns

### 8.1 Time as the shared cursor

Everything in `ResultsView` is bound to `currentTime: number`. There is no
event bus. The lift-state-up pattern lives in `ResultsView.tsx`:

```tsx
const [currentTime, setCurrentTime] = useState(0)
const currentROI = useMemo(() => getCurrentROI(data.frames, currentTime), [data.frames, currentTime])
const handleSeek = (t: number) => setCurrentTime(t)
```

- `VideoPlayer` reports `onTimeUpdate(t)` → updates `currentTime`.
- `BrainChart` reports `onSeek(t)` (click on timeline) → updates `currentTime`.
- `InsightCard` reports `onSeek(t0)` (click on card) → updates `currentTime`.
- `Brain3D` consumes `currentROI` (derived) and renders.

### 8.2 Click-to-seek

Every timestamp on screen is a click target.

- Insight cards: click anywhere on the card → seek to `timestamp_range_s[0]`.
- Chart points: click on a marker → seek to that frame's `t_s`.
- Assessment timestamps (e.g., "t=14s" inside the summary paragraph): if we
  link these later, parse with a regex matching `t=(\d+(?:\.\d+)?)s` and
  insert `<button>` tags that call `setCurrentTime`.

### 8.3 Hover sync (Phase 4)

When the user hovers an insight card, the brain card should highlight the
parcels for the cited ROIs. Plumbing:

- `ResultsView` adds `const [hoveredROIs, setHoveredROIs] = useState<ROIKey[]>([])`.
- `InsightCard` accepts `onHover(rois: ROIKey[])` and `onLeave()` props.
- `BrainCortical` accepts `highlightedROIs: ROIKey[]`.
- Same pattern flows the other way: hover a region on the brain → dim insight
  cards that don't cite that region.

### 8.4 Keyboard

- `Space` toggles video playback (HTML5 default — ensure the video element keeps focus).
- Arrow keys: 5s seek backward/forward when the video element has focus.
- `Escape`: only inside `AnalyzingView` — cancels the in-flight analysis (already wired via `AbortController`).

### 8.5 Empty / loading / error states

| Surface | Empty | Loading | Error |
|---|---|---|---|
| Insights grid | "No notable moments detected. The demo may be too short or too uniform." (already in `ResultsView.tsx:130-132`) | N/A — insights arrive with the response | Surfaced via the analyzing-state error panel |
| Brain card | Pulsing soft orb (`BrainFallback`) — already shipped | Same fallback | If GLB fails to load, fall back to `Brain3D.tsx` placeholder + small "rendering simplified" caption |
| Chart | "Timeline unavailable." | Subtle grid skeleton | Same as brain — caption + reduced display |
| Video | "No video" placeholder (already in `VideoPlayer.tsx:80-92`) | Browser-native | If file invalid, surface in the analyzing state |
| Assessment | "Assessment unavailable." | N/A | Same — analyzing-state error |

---

## 9. Accessibility

These are the rules. None are optional.

### 9.1 Keyboard

- Every interactive element must be reachable via Tab in document order.
- Focus rings are visible — `:focus-visible` outline `2px solid var(--primary)` on all buttons and panels with `tabIndex`.
- `InsightCard` is currently a `<motion.button>` — keep it as `button`, never demote to `div + onClick`.
- Modal-style overlays (none today) would need focus traps when added.

### 9.2 Screen readers

- Page sections use semantic landmarks: `<main>`, `<nav>`, `<section>`. `ResultsView.tsx` should wrap its content in `<main>` (currently a `<div>` — TODO).
- Brain card: `<div role="img" aria-label="3D brain visualization. Drag to rotate.">` on the canvas wrapper.
- Live regions: the analyzing state's status text uses `aria-live="polite"` so progress updates announce.
- Insights: each card has an implicit accessible name from its content. Add `aria-label="Insight at {start}–{end} seconds"` for clarity if the visible text is long.

### 9.3 Contrast

Body text on `--bg`: ratio is ~14:1 (`#e8eaf0` on `#0B0F14`). Way above the 4.5:1 minimum.

Watch out:
- `rgba(255,255,255,0.30)` on `--bg` is 4.4:1 — borderline. Use only for non-essential captions.
- `rgba(255,255,255,0.45)` is fine (~6.5:1).
- Per-ROI colors against `--bg`: all pass 4.5:1 except `#7C9CFF` at exactly 4.5:1. Use for fills, not body text.

### 9.4 Touch targets

44px minimum hit area for any clickable element. Insight cards, nav buttons,
chart markers. Pad with `padding` rather than expanding visible content.

### 9.5 Reduced motion

Respect `prefers-reduced-motion: reduce`:

```tsx
const prefersReducedMotion = useReducedMotion() // framer-motion hook
const transition = prefersReducedMotion ? { duration: 0 } : { duration: 0.4 }
```

Applies to: page transitions, brain idle drift, ambient blob motion. Never to
the video timeline (the user is in control).

---

## 10. Responsive Behavior

Desktop-first. Breakpoints:

| Breakpoint | Behavior |
|---|---|
| `≥ 1280px` | Default layout. Brain card sits beside video at `50vh × 50vh`. |
| `1024px–1279px` | Same layout. Container shrinks naturally; panels reflow within `max-w-6xl`. |
| `768px–1023px` | Stack: video on top (`100vw × 50vh`), brain below (square). 3-col bottom grid becomes 2-col (insights + assessment). |
| `< 768px` | Full block: "Open this on desktop. Aesthesis is desktop-first today." Don't try to cram a useful experience here yet. |

Implementation: Tailwind responsive prefixes (`md:`, `lg:`, `xl:`). The
`flex gap-5 items-stretch` row in `ResultsView.tsx:74` becomes
`flex-col lg:flex-row gap-5 items-stretch` to stack at `<1024px`.

---

## 11. Performance Budget

| Metric | Budget | Notes |
|---|---|---|
| First contentful paint | < 1.5s | Trivial today — landing is small. |
| Time to interactive (results page) | < 500ms after `analyze()` resolves | Depends on Brain3D / BrainCortical load. Lazy-loaded already via `React.lazy`. |
| Brain bundle (JS) | < 200 KB gzipped delta | `@react-three/fiber` + drei + three are already in the bundle for the placeholder. `BrainCortical.tsx` adds ~5 KB. The colormap util adds ~2 KB. |
| Brain bundle (assets) | < 4 MB total, ~2 MB gzipped | Four GLBs at ~500 KB–1 MB each, served from `public/brain/`, cached by the browser after first load. |
| Re-render on time scrub | < 16ms (60fps) | `useMemo` on `currentROI` is required. Verify with React DevTools profiler. |
| Network for `/api/analyze` | ~6–13s wall time, ~50–500 KB payload | Acceptable for the use case. Add timing telemetry on real users. |
| TRIBE WS frame stream | ~5 fps, JPEG quality 60 | Lossy is fine — these are progress hints, not the final video. |

---

## 12. Testing & Verification

### 12.1 Type-check

`cd aesthesis-app && npx tsc --noEmit` — must be clean. Run before every commit.

### 12.2 Build

`cd aesthesis-app && npm run build` — must succeed with no warnings. ESLint
warnings are warnings; build errors block.

### 12.3 Manual (golden path)

After any UI change:

1. `./dev.sh` (or `dev.cmd` on Windows). Confirm both backend and frontend up.
2. Open `http://localhost:3000`.
3. Drop a sample MP4 (use `IMG_6217.mp4` in repo root for canonical testing).
4. Wait for analysis. Confirm:
   - Video plays.
   - Brain rotates on drag.
   - Insight cards render with timestamps inside `[0, duration_s]`.
   - Click an insight → video seeks.
   - Click a chart point → video seeks.
   - Hover an insight → (Phase 4) brain regions highlight.
5. Reload mid-analysis → ensure abort works cleanly (no zombie request, no
   error banner stuck on screen).

### 12.4 Visual regression

Lacking automated tools, take a screenshot of the Results page on a known
sample after each visual change. Diff by eye against the previous shot. If
this becomes painful, add Playwright + `toMatchSnapshot()` in a follow-up.

### 12.5 Common breakages

| Symptom | Likely cause |
|---|---|
| Insight card timestamp > video length | Backend not clamping. Check `synthesizer.py` `_clamp_insight_range` is wired. |
| Video frame fills viewport | Outer `flex-1 aspect-video` — kill the `aspect-video` on the inner. |
| Brain ends up in bottom-right corner | Row's `items-stretch` with no height constraint on the brain panel. Square it. |
| WS frame stream silent | `wss://` mismatch with `http://localhost`. Use `ws://` in dev. |
| `window is not defined` | Code reading `window` at module top level. Move inside `useEffect` or guard with `typeof window`. |

---

## 13. Implementation Order (Sprint Plan)

### Sprint 1 — Foundation polish (now → 2 days)

- [ ] §9 a11y pass — add semantic landmarks, ARIA labels, `prefers-reduced-motion` honor.
- [ ] §10 responsive — implement the `lg:` stacking + the `<768px` "desktop-only" message.
- [ ] §11 performance — verify lazy-load works, add a screenshot of bundle stats to the PR.
- [ ] §12.1–12.3 — write the manual checklist into `aesthesis-app/README.md`.

### Sprint 2 — Real brain (Phase 1–4, ~2 days)

- [ ] §7 Phase 1 — backend parcel emission.
- [ ] §7 Phase 2 — GLB bake script + first GLB committed (or .gitignored with build doc).
- [ ] §7 Phase 3 — `BrainCortical.tsx` swap (no new deps; uses existing `@react-three/fiber` + drei).
- [ ] §7 Phase 4 — camera presets, hover readout, insight ↔ brain link.

### Sprint 3 — Polish & extras

- [ ] §8.3 hover sync end-to-end.
- [ ] Insight card "explain this" expansion (Gemini's `cited_brain_features` shown inline).
- [ ] Assessment's `decisive_moment` rendered as a clickable timestamp.
- [ ] Chart redesign once we know the brain visualization is the centerpiece (less line-chart, more scrub-bar with annotations?).

### Sprint 4 — Mobile (later)

- [ ] Real mobile design. Probably a vertical stack with a sticky brain widget.
- [ ] Lower-poly GLB tier for mobile (decimate fsaverage5 → ~5k vertices), or render the brain to a static thumbnail and skip the live mesh entirely under a width breakpoint.

---

## 14. Open Questions

These are not blockers for any current sprint — capture them so they don't
get lost.

1. **Onboarding for the brain visualization.** First-time users won't know
   what they're looking at. Tooltip on first load? A small "What am I seeing?"
   chip linking to a 4-line explainer? Decide in Sprint 2.
2. **Insight density.** Today we show all insights as cards. If a 30s clip
   has 12 insights, the grid gets crowded. Limit to top-N by magnitude?
   Surface the rest in an expander?
3. **Goal echo.** The user's stated `goal` is in the request and used for
   prompt grounding, but we never echo it back in the UI. Consider showing
   it above the assessment panel: "You asked: '...'. Here's what we found."
4. **Run history.** Today every analysis is stateless. Adding a localStorage
   list of past runs (with file metadata, run_id, and timestamps) would let
   users go back without re-uploading. ~half a day; non-blocking.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **TRIBE v2** | Meta's transformer encoder-decoder for video → fMRI prediction. Trained on movie-watching subjects. |
| **fsaverage5** | Standard FreeSurfer cortical mesh, 20,484 vertices total (10,242 per hemisphere). The TRIBE output space. |
| **TR (repetition time)** | One fMRI sample. TRIBE's TR is 1.5s; the model emits one prediction frame per TR. |
| **Schaefer-400** | A 400-parcel cortical atlas. Each vertex belongs to one parcel. |
| **Yeo network** | A coarse functional grouping of cortex (DMN, Visual, Limbic, etc.). The 8 ROIs are mixtures of Yeo networks weighted by Neurosynth term maps. |
| **ROI** | "Region of interest" — one of 8 UX-tuned brain signals (`aesthetic_appeal`, …, `friction_anxiety`). |
| **Insight** | One Gemini-generated `(timestamp_range_s, ux_observation, recommendation, cited_brain_features, cited_screen_moment)` tuple. |
| **Event** | A deterministic-pattern match on the TRIBE timeline (spike, dominant_shift, sustained, co_movement, trough, flow, bounce_risk). Feeds Gemini. |
| **Composite** | A derived score computed from ROIs (`appeal_index`, `flow_state`, `bounce_risk`, …). |
| **Frame** (in this codebase) | One UI-side `{ t_s, values }` snapshot. Not the same as a video frame and not the same as a TRIBE prediction frame. |

---

**Owners.** Frontend lead owns this doc. Update it in the same PR as any
visual change. Stale UI docs are worse than no UI docs — when this doc
disagrees with the code, fix the doc the same day.
