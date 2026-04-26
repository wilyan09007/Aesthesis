// Diverging colormap for cortical brain rendering.
//
// TRIBE v2 emits z-scored activations per parcel. Z=0 means "neutral
// w.r.t. this clip's baseline." We want a color scheme where:
//   - z = 0          → neutral mid-tone (so dim and "no signal" look the same)
//   - z > 0          → warm shift (red), proportional to magnitude
//   - z < 0          → cool shift (blue), proportional to magnitude
//   - extreme |z|    → saturated, but not flat — readable shading
//
// We use a simplified RdBu_r ("reverse red-blue") palette: blue at z=-2,
// neutral gray at z=0, red at z=+2. Squashed through tanh so |z| > 3
// doesn't blow out the visual.
//
// ASSUMPTIONS_BRAIN.md §3.2 + §1.3.

import type { ROIValues } from "./types"

export type RGB = [number, number, number]

// Three anchor colors. RGB in [0, 1]. The neutral color is intentionally
// muted (not pure gray) so the cortical mesh doesn't read as "broken
// monitor" when activity is near zero.
const COLD: RGB = [0.16, 0.36, 0.78]   // saturated blue
const NEUTRAL: RGB = [0.55, 0.55, 0.62] // muted gray-violet
const WARM: RGB = [0.85, 0.18, 0.18]    // saturated red

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

function lerp3(a: RGB, b: RGB, t: number): RGB {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]
}

// Squash z-scores through tanh so |z| > 3 saturates. The factor 0.55
// puts |z|=2 at ~0.83 of the gradient and |z|=1 at ~0.50 — enough
// dynamic range without making weak signals invisible.
function squashZ(z: number): number {
  if (!Number.isFinite(z)) {
    // Loud failure mode: NaN-tinted vertices look obviously wrong.
    // Caller should treat this as a bug in upstream data.
    return 0
  }
  return Math.tanh(z * 0.55)
}

/**
 * Map a z-score to an RGB color (each channel in [0,1]).
 *
 * z = 0 → NEUTRAL.
 * z > 0 → interpolate NEUTRAL → WARM.
 * z < 0 → interpolate NEUTRAL → COLD.
 *
 * Gracefully handles NaN/Inf by returning NEUTRAL — the alternative is
 * propagating black or white pixels, which read as "the brain is broken"
 * instead of "no data here."
 */
export function divergingColor(z: number): RGB {
  const t = squashZ(z) // in [-1, 1]
  if (t >= 0) {
    return lerp3(NEUTRAL, WARM, t)
  }
  return lerp3(NEUTRAL, COLD, -t)
}

/**
 * Mix curvature-based shading into a base color.
 *
 * sulc is normalized to roughly [-1, 1] at bake time
 * (`bake_brain_glbs.py:_normalize_sulc`). Negative = sulcus (recessed),
 * positive = gyrus (raised). We darken sulci slightly so the cortical
 * folds read visually, the same trick Meta's TRIBE v2 demo uses (per the
 * reverse-engineered bundle, see UIUX.md §7.0).
 *
 * The mix factor (0.35) was chosen empirically: enough to make sulci
 * visible without overwhelming the activation signal.
 */
export function shadeBySulc(rgb: RGB, sulc: number): RGB {
  const s = Number.isFinite(sulc) ? sulc : 0
  // smoothstep -0.5 → 0.5 keeps the multiplier in [0.65, 1.0].
  const x = Math.min(1, Math.max(0, (s + 0.5) / 1.0))
  const t = x * x * (3 - 2 * x) // smoothstep
  const factor = 1 - 0.35 * (1 - t)
  return [rgb[0] * factor, rgb[1] * factor, rgb[2] * factor]
}

/**
 * Pick a representative ROI z-score for a parcel when we want to color
 * the placeholder brain (no per-parcel data available). Uses the most
 * extreme |z| across the 8 ROIs so the dominant signal at this moment
 * shows through.
 *
 * This is an escape hatch — only invoked by the placeholder code path
 * in BrainCortical.tsx when `parcelSeries` is null. Real cortical
 * rendering bypasses this entirely.
 */
export function pickDominantROI(roi: ROIValues | undefined): number {
  if (!roi) return 0
  let best = 0
  for (const v of Object.values(roi)) {
    if (Math.abs(v) > Math.abs(best)) best = v
  }
  return best
}
