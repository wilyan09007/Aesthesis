// Client-side synthetic activation generator for the hero brain.
// Produces a FaceColors object byte-identical in shape to what the
// backend's tribe_neural/steps/step2c_face_colors.py would emit, so
// BrainScene renders the hero with the same shader / wire format.
//
// Approach: discrete activation events anchored to 3D seed points on
// the cortex. Each event has a rise/peak/decay temporal envelope and
// a gaussian falloff in actual 3D mm-distance from its seed.
//
// Why not face-index gaussians? freesurfer's fsaverage5 tessellation
// order is not strongly index-coherent — adjacent face indices can
// land far apart on the surface. Using face-index space produces
// scattered triangle clusters (the "tropophobic" pattern). Using true
// 3D centroid distance produces small contiguous patches that
// genuinely light up and decay in place.
//
// Centroids come from the GLBs the renderer already ships
// (public/brain/fsaverage5-{left,right}-pial.glb). They're loaded once,
// cached at module level, and shared across remounts. THREE.Cache is
// enabled so the second GLB fetch (by BrainScene.loadHemispheres) is
// a cache hit.

import * as THREE from "three"
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js"

import type { HemisphereFaceColors } from "./types"

const LEFT_PIAL = "/brain/fsaverage5-left-pial.glb"
const RIGHT_PIAL = "/brain/fsaverage5-right-pial.glb"

// v4 colormap constants — keep in lockstep with
// tribe_service/tribe_neural/steps/step2c_face_colors.py.
const WHITE_BASE: readonly [number, number, number] = [0.99, 0.97, 0.93]
const NEON_RED: readonly [number, number, number] = [1.0, 0.05, 0.24]
const Z_THRESH = 0.2
const Z_MAX = 1.5
const BASE_ALPHA = 0.18
const MAX_ALPHA = 0.95

function colormap(z: number): [number, number, number, number] {
  const pz = z > 0 ? z : 0
  const pt = pz < Z_MAX ? pz / Z_MAX : 1
  const r = WHITE_BASE[0] + (NEON_RED[0] - WHITE_BASE[0]) * pt
  const g = WHITE_BASE[1] + (NEON_RED[1] - WHITE_BASE[1]) * pt
  const b = WHITE_BASE[2] + (NEON_RED[2] - WHITE_BASE[2]) * pt

  let alpha: number
  if (pz < Z_THRESH) {
    alpha = BASE_ALPHA
  } else {
    const span = Z_MAX - Z_THRESH
    const ramp = Math.pow(Math.min(1, (pz - Z_THRESH) / span), 0.75)
    alpha = BASE_ALPHA + ramp * (MAX_ALPHA - BASE_ALPHA)
  }

  return [
    Math.min(255, Math.max(0, Math.round(r * 255))),
    Math.min(255, Math.max(0, Math.round(g * 255))),
    Math.min(255, Math.max(0, Math.round(b * 255))),
    Math.min(255, Math.max(0, Math.round(alpha * 255))),
  ]
}

function mulberry32(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ""
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    const chunk = bytes.subarray(i, Math.min(i + CHUNK, bytes.length))
    binary += String.fromCharCode.apply(null, chunk as unknown as number[])
  }
  return btoa(binary)
}

// ─── Centroid extraction ────────────────────────────────────────────────────

async function loadFaceCentroids(url: string): Promise<Float32Array> {
  const loader = new GLTFLoader()
  const gltf = await loader.loadAsync(url)
  let mesh: THREE.Mesh | null = null
  gltf.scene.traverse((obj) => {
    if (!mesh && obj instanceof THREE.Mesh) mesh = obj
  })
  if (!mesh) throw new Error(`brainSynthetic: no Mesh found in ${url}`)

  const m = mesh as THREE.Mesh
  const positions = m.geometry.attributes.position.array as Float32Array
  const index = m.geometry.index
  if (!index) throw new Error(`brainSynthetic: no index buffer in ${url}`)
  const indices = index.array

  const nFaces = indices.length / 3
  const centroids = new Float32Array(nFaces * 3)
  for (let f = 0; f < nFaces; f++) {
    const i0 = indices[f * 3] * 3
    const i1 = indices[f * 3 + 1] * 3
    const i2 = indices[f * 3 + 2] * 3
    centroids[f * 3]     = (positions[i0]     + positions[i1]     + positions[i2])     / 3
    centroids[f * 3 + 1] = (positions[i0 + 1] + positions[i1 + 1] + positions[i2 + 1]) / 3
    centroids[f * 3 + 2] = (positions[i0 + 2] + positions[i1 + 2] + positions[i2 + 2]) / 3
  }
  return centroids
}

let centroidsPromise: Promise<{ left: Float32Array; right: Float32Array }> | null = null

/** Load fsaverage5 face centroids (LH + RH), cached per session. */
export function getFaceCentroids(): Promise<{ left: Float32Array; right: Float32Array }> {
  if (!centroidsPromise) {
    // Cache the fetch so BrainScene.loadHemispheres re-uses it (decode is
    // still per-call but the network round-trip happens once).
    THREE.Cache.enabled = true
    centroidsPromise = (async () => {
      const [left, right] = await Promise.all([
        loadFaceCentroids(LEFT_PIAL),
        loadFaceCentroids(RIGHT_PIAL),
      ])
      return { left, right }
    })()
  }
  return centroidsPromise
}

// ─── Event generation ───────────────────────────────────────────────────────

interface ActivationEvent {
  centerIdx: number       // seed face index
  startFrame: number      // frame at which envelope begins (modular)
  durationFrames: number  // total active span (rise + peak + decay)
  sigma: number           // gaussian width in mesh units (mm in MNI)
  peakAmp: number         // peak z at the seed centroid
}

/** Pick `count` seed face indices with at least `minSep` mm of 3D separation. */
function pickSeeds(
  rng: () => number,
  centroids: Float32Array,
  count: number,
  minSep: number,
): number[] {
  const N = centroids.length / 3
  const seeds: number[] = []
  const minSepSq = minSep * minSep
  let attempts = 0
  while (seeds.length < count && attempts < count * 100) {
    attempts++
    const c = Math.floor(rng() * N)
    const cx = centroids[c * 3]
    const cy = centroids[c * 3 + 1]
    const cz = centroids[c * 3 + 2]
    let ok = true
    for (const s of seeds) {
      const dx = cx - centroids[s * 3]
      const dy = cy - centroids[s * 3 + 1]
      const dz = cz - centroids[s * 3 + 2]
      if (dx * dx + dy * dy + dz * dz < minSepSq) {
        ok = false
        break
      }
    }
    if (ok) seeds.push(c)
  }
  return seeds
}

function generateEvents(
  rng: () => number,
  centroids: Float32Array,
  nFrames: number,
  count: number,
): ActivationEvent[] {
  // 25 mm minimum separation ≈ a major sulcus apart on fsaverage5 — far
  // enough that simultaneous events read as distinct regions.
  const seeds = pickSeeds(rng, centroids, count, 25)

  // Uniformly random start frames across the loop. Earlier we staggered
  // them evenly with jitter, but at higher event counts the regular
  // cadence read as predictable; uniform random gives natural clustering
  // and lulls — closer to spontaneous neural activity.
  const out: ActivationEvent[] = []
  for (let i = 0; i < seeds.length; i++) {
    out.push({
      centerIdx: seeds[i],
      startFrame: Math.floor(rng() * nFrames),
      // 8-14 frames at TR=0.25 → 2.0-3.5 s per event. Shorter pulses
      // mean more events fit in the loop without piling up; the 0→1→0
      // sin envelope still reads cleanly at this duration.
      durationFrames: 8 + Math.floor(rng() * 7),
      // 7-11 mm sigma → effective patch radius ~2σ ≈ 14-22 mm
      sigma: 7 + rng() * 4,
      // Pushes past Z_MAX = 1.5 at peak ⇒ saturated neon red core.
      peakAmp: 1.8 + rng() * 0.7,
    })
  }
  return out
}

// ─── Bake ───────────────────────────────────────────────────────────────────

function bakeHemisphere(
  centroids: Float32Array,
  events: ActivationEvent[],
  nFrames: number,
): { bytes: Uint8Array; nFaces: number } {
  const nFaces = centroids.length / 3
  const data = new Uint8Array(nFrames * nFaces * 4)
  const z = new Float32Array(nFaces)

  // Per-event spatial parameters, hoisted out of the inner loop.
  const eventCount = events.length
  const cx = new Float32Array(eventCount)
  const cy = new Float32Array(eventCount)
  const cz = new Float32Array(eventCount)
  const inv2sigmaSq = new Float32Array(eventCount)
  const cutoffSq = new Float32Array(eventCount)
  for (let e = 0; e < eventCount; e++) {
    const idx = events[e].centerIdx * 3
    cx[e] = centroids[idx]
    cy[e] = centroids[idx + 1]
    cz[e] = centroids[idx + 2]
    inv2sigmaSq[e] = 1 / (2 * events[e].sigma * events[e].sigma)
    // 2.5σ cutoff — gaussian < 0.044 beyond. Faces past this contribute
    // almost nothing visually; skipping saves the exp() call.
    const cutoff = 2.5 * events[e].sigma
    cutoffSq[e] = cutoff * cutoff
  }

  for (let f = 0; f < nFrames; f++) {
    z.fill(0)
    for (let e = 0; e < eventCount; e++) {
      const ev = events[e]
      // Wrapped time within the event — the loop is cyclic so an event
      // starting near nFrames-1 with duration 15 still completes its
      // envelope across the wraparound.
      let dt = f - ev.startFrame
      if (dt < 0) dt += nFrames
      if (dt >= ev.durationFrames) continue
      const tNorm = dt / ev.durationFrames
      const envelope = Math.sin(Math.PI * tNorm) // 0 → 1 → 0 across the event
      if (envelope <= 0) continue
      const amp = ev.peakAmp * envelope
      const inv = inv2sigmaSq[e]
      const cutSq = cutoffSq[e]
      const ex = cx[e], ey = cy[e], ez = cz[e]
      // 3D distance from this event's centroid to every face centroid.
      // Brute force is fine at 20480 faces — ~120 µs per event-frame.
      for (let i = 0; i < nFaces; i++) {
        const dx = centroids[i * 3]     - ex
        const dy = centroids[i * 3 + 1] - ey
        const dz = centroids[i * 3 + 2] - ez
        const d2 = dx * dx + dy * dy + dz * dz
        if (d2 > cutSq) continue
        z[i] += amp * Math.exp(-d2 * inv)
      }
    }
    const frameBase = f * nFaces * 4
    for (let i = 0; i < nFaces; i++) {
      const [r, g, b, a] = colormap(z[i])
      const idx = frameBase + i * 4
      data[idx] = r
      data[idx + 1] = g
      data[idx + 2] = b
      data[idx + 3] = a
    }
  }
  return { bytes: data, nFaces }
}

// ─── Public API ─────────────────────────────────────────────────────────────

export interface SyntheticOptions {
  // Total frames per loop. Default 60 → 15 s loop at trDurationS=0.25.
  nFrames?: number
  // Activation events per hemisphere. Default 8 — well-separated seeds,
  // each ~2-3.5 s long, randomly placed across the loop so 1-3 patches
  // are typically active at once.
  eventsPerHemi?: number
  // RNG seeds. Different per hemi keeps L/R asymmetric.
  seedLeft?: number
  seedRight?: number
}

export function buildSyntheticFromCentroids(
  centroids: { left: Float32Array; right: Float32Array },
  opts: SyntheticOptions = {},
): { left: HemisphereFaceColors; right: HemisphereFaceColors } {
  const nFrames = opts.nFrames ?? 60
  const eventsPerHemi = opts.eventsPerHemi ?? 8
  const lhRng = mulberry32(opts.seedLeft ?? 0x9b1d)
  const rhRng = mulberry32(opts.seedRight ?? 0x3a4f)

  const lhEvents = generateEvents(lhRng, centroids.left, nFrames, eventsPerHemi)
  const rhEvents = generateEvents(rhRng, centroids.right, nFrames, eventsPerHemi)

  const lh = bakeHemisphere(centroids.left, lhEvents, nFrames)
  const rh = bakeHemisphere(centroids.right, rhEvents, nFrames)

  return {
    left: {
      format: "uint8_rgba_bin",
      shape: [nFrames, lh.nFaces, 4],
      n_frames: nFrames,
      n_faces: lh.nFaces,
      data_b64: bytesToBase64(lh.bytes),
    },
    right: {
      format: "uint8_rgba_bin",
      shape: [nFrames, rh.nFaces, 4],
      n_frames: nFrames,
      n_faces: rh.nFaces,
      data_b64: bytesToBase64(rh.bytes),
    },
  }
}
