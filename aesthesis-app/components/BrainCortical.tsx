"use client"

// Real cortical brain visualization — fsaverage5 inflated mesh, colored
// per-parcel by TRIBE v2 z-scored activations from the current TR.
//
// Architecture: ASSUMPTIONS_BRAIN.md §3.
// Data flow:
//   1. Two GLBs load via drei `useGLTF` (left + right hemisphere).
//   2. On first render, we read the baked custom attributes _PARCELID
//      (per-vertex Schaefer-400 index) and _SULC (curvature) off each
//      mesh's BufferGeometry.
//   3. We allocate a per-vertex COLOR_0 attribute, sized (n_vertices, 3).
//   4. On every change to `tIndex` or `parcelSeries`, we walk vertices,
//      look up the parcel's z-score for this TR, run through the
//      diverging colormap, mix in sulcal shading, write to COLOR_0,
//      flag `needsUpdate`. The GPU re-uploads the buffer on the next
//      frame.
//
// Verbose console logging at every lifecycle hook (mount, GLB load,
// per-TR coloring after the first 5 are logged at info level then
// debounced). Prefix `[brain-cortical]` for grep-ability.
//
// Fallback: if `parcelSeries` is null OR the GLBs fail to load, we
// silently render the legacy `Brain3D` placeholder so the panel never
// shows a void. ASSUMPTIONS_BRAIN.md §3.6.

import { Suspense, useEffect, useMemo, useRef } from "react"
import { Canvas, useFrame } from "@react-three/fiber"
import { OrbitControls, useGLTF } from "@react-three/drei"
import * as THREE from "three"

import type { ROIValues } from "@/lib/types"
import { divergingColor, shadeBySulc } from "@/lib/colormap"
import Brain3D from "./Brain3D"

const LEFT_INFLATED = "/brain/fsaverage5-left-inflated.glb"
const RIGHT_INFLATED = "/brain/fsaverage5-right-inflated.glb"
const LEFT_PIAL = "/brain/fsaverage5-left-pial.glb"
const RIGHT_PIAL = "/brain/fsaverage5-right-pial.glb"

// Pre-warm both inflated GLBs as soon as this module imports. drei
// `useGLTF.preload` triggers a fetch + parse off the critical render
// path, so by the time the user reaches the Results page the geometry
// is usually already in cache. Cheap insurance against first-paint pop.
useGLTF.preload(LEFT_INFLATED)
useGLTF.preload(RIGHT_INFLATED)

interface BrainCorticalProps {
  /** (n_TRs, 400) Schaefer-400 z-scored activations. Null => fallback. */
  parcelSeries: number[][] | null
  /** Index into parcelSeries — typically `floor(currentTime / tr_duration_s)`. */
  tIndex: number
  /** ROI values used by the placeholder fallback only. */
  roiValues?: ROIValues
  /** Surface variant. Inflated reads better; pial is anatomical. */
  variant?: "inflated" | "pial"
  /** Optional fixed pixel size. If omitted, fills parent. */
  size?: number
}

interface HemisphereProps {
  url: string
  parcelSeries: number[][]
  tIndex: number
  hemiLabel: "lh" | "rh"
}

function logCortex(level: "info" | "warn" | "error", msg: string, extra?: unknown) {
  // eslint-disable-next-line no-console
  console[level](`[brain-cortical] ${msg}`, extra ?? "")
}

/**
 * One hemisphere mesh with its color attribute driven by `parcelSeries[tIndex]`.
 *
 * Why a separate component per hemisphere: each side has its own GLB and
 * its own BufferGeometry. Sharing one pair of material/buffer refs across
 * both meshes is messier than just instantiating the component twice.
 */
function HemisphereMesh({ url, parcelSeries, tIndex, hemiLabel }: HemisphereProps) {
  const { scene } = useGLTF(url)
  const meshRef = useRef<THREE.Mesh | null>(null)
  const colorAttrRef = useRef<THREE.BufferAttribute | null>(null)
  const parcelIdRef = useRef<Uint16Array | null>(null)
  const sulcRef = useRef<Float32Array | null>(null)
  const lastLoggedTRef = useRef<number | null>(null)
  const colorWriteCountRef = useRef(0)

  // ── Find the mesh inside the loaded scene ───────────────────────────────
  // GLBs ship as a Scene with a Node hierarchy. We walk it once to grab
  // the first Mesh. The bake script writes exactly one mesh per GLB.
  const mesh = useMemo<THREE.Mesh | null>(() => {
    let found: THREE.Mesh | null = null
    scene.traverse((obj: THREE.Object3D) => {
      if (!found && obj instanceof THREE.Mesh) found = obj
    })
    if (!found) {
      logCortex("error", `${hemiLabel}: no Mesh found in GLB at ${url}`)
      return null
    }
    return found
  }, [scene, url, hemiLabel])

  // ── On mount: read custom attributes, allocate color buffer ────────────
  useEffect(() => {
    if (!mesh) return
    meshRef.current = mesh
    const geom = mesh.geometry as THREE.BufferGeometry

    const parcelAttr = geom.getAttribute("_PARCELID") as THREE.BufferAttribute | undefined
    const sulcAttr = geom.getAttribute("_SULC") as THREE.BufferAttribute | undefined
    const positionAttr = geom.getAttribute("position") as THREE.BufferAttribute | undefined
    const nVertices = positionAttr?.count ?? 0

    logCortex("info", `${hemiLabel}: loaded GLB ${url}`, {
      vertices: nVertices,
      faces: (geom.index?.count ?? 0) / 3,
      hasParcelId: !!parcelAttr,
      hasSulc: !!sulcAttr,
    })

    if (!parcelAttr) {
      // GLTFLoader is supposed to preserve _PARCELID per ASSUMPTIONS_BRAIN.md §2.2.
      // Loud error so this regression is obvious in the console.
      logCortex(
        "error",
        `${hemiLabel}: GLB missing _PARCELID custom attribute. ` +
          "Bake script may be out of date. Check tribe_service/scripts/bake_brain_glbs.py.",
      )
      return
    }
    if (!sulcAttr) {
      logCortex(
        "warn",
        `${hemiLabel}: GLB missing _SULC; sulcal shading disabled (mesh will look flat).`,
      )
    }

    // Cast custom attribute arrays into the typed-array form we need.
    parcelIdRef.current = new Uint16Array(parcelAttr.array)
    sulcRef.current = sulcAttr ? new Float32Array(sulcAttr.array) : null

    // Allocate vertex color buffer if not already present. This is what
    // we mutate per TR change.
    let colorAttr = geom.getAttribute("color") as THREE.BufferAttribute | undefined
    if (!colorAttr || colorAttr.itemSize !== 3 || colorAttr.count !== nVertices) {
      colorAttr = new THREE.BufferAttribute(new Float32Array(nVertices * 3), 3)
      geom.setAttribute("color", colorAttr)
    }
    colorAttr.setUsage(THREE.DynamicDrawUsage)
    colorAttrRef.current = colorAttr

    // The GLB's MeshStandardMaterial may have been baked without
    // vertexColors enabled. Force it on so our color attribute drives
    // the surface tint.
    const mat = mesh.material as THREE.MeshStandardMaterial | THREE.MeshStandardMaterial[]
    const apply = (m: THREE.MeshStandardMaterial) => {
      m.vertexColors = true
      // Default GLB material is often pure white; tone down to neutral so
      // unlit areas don't blow out.
      m.color.setRGB(1.0, 1.0, 1.0)
      m.roughness = 0.8
      m.metalness = 0.05
      m.needsUpdate = true
    }
    if (Array.isArray(mat)) mat.forEach(apply)
    else apply(mat)

    logCortex("info", `${hemiLabel}: color attribute initialized`, {
      count: nVertices,
      dynamicUsage: true,
    })
  }, [mesh, url, hemiLabel])

  // ── Per-TR color update ────────────────────────────────────────────────
  // useFrame fires every render frame (~60Hz). We only do real work when
  // tIndex actually changed (or is the first run). Cheap branch on the
  // hot path.
  useFrame(() => {
    const colorAttr = colorAttrRef.current
    const parcelIds = parcelIdRef.current
    if (!colorAttr || !parcelIds) return
    if (lastLoggedTRef.current === tIndex) return

    const tr = parcelSeries[tIndex]
    if (!tr) {
      // Out-of-range tIndex. Could be a startup race; do nothing this
      // frame and try again. Don't log every frame.
      return
    }

    const sulc = sulcRef.current
    const colors = colorAttr.array as Float32Array

    const n = parcelIds.length
    for (let i = 0; i < n; i++) {
      const pid = parcelIds[i]
      // pid 0 = unassigned vertex (rare; midline cuts). Color it a deep
      // neutral so it visibly differs from active cortex.
      const z = pid > 0 ? tr[pid - 1] ?? 0 : 0
      let rgb = divergingColor(z)
      if (sulc) rgb = shadeBySulc(rgb, sulc[i])
      colors[i * 3] = rgb[0]
      colors[i * 3 + 1] = rgb[1]
      colors[i * 3 + 2] = rgb[2]
    }
    colorAttr.needsUpdate = true
    lastLoggedTRef.current = tIndex

    // First 5 changes log at info; after that debug-level only (debounced
    // via the simple counter rather than chatter on every scrub).
    colorWriteCountRef.current += 1
    if (colorWriteCountRef.current <= 5) {
      logCortex("info", `${hemiLabel}: colored TR ${tIndex}`, {
        n_vertices: n,
        z_at_first_assigned: parcelIds[0] > 0 ? tr[parcelIds[0] - 1] : null,
      })
    }
  })

  if (!mesh) return null
  return <primitive object={mesh} />
}

function CorticalScene({
  parcelSeries,
  tIndex,
  variant = "inflated",
}: {
  parcelSeries: number[][]
  tIndex: number
  variant: "inflated" | "pial"
}) {
  const leftUrl = variant === "inflated" ? LEFT_INFLATED : LEFT_PIAL
  const rightUrl = variant === "inflated" ? RIGHT_INFLATED : RIGHT_PIAL

  return (
    <>
      <ambientLight intensity={0.45} />
      <directionalLight position={[3, 3, 3]} intensity={0.8} color="#a0b8ff" />
      <directionalLight position={[-3, -2, -3]} intensity={0.3} color="#5CF2C5" />

      <HemisphereMesh url={leftUrl} parcelSeries={parcelSeries} tIndex={tIndex} hemiLabel="lh" />
      <HemisphereMesh url={rightUrl} parcelSeries={parcelSeries} tIndex={tIndex} hemiLabel="rh" />

      <OrbitControls enableRotate enableZoom={false} enablePan={false} />
    </>
  )
}

export default function BrainCortical({
  parcelSeries,
  tIndex,
  roiValues,
  variant = "inflated",
  size,
}: BrainCorticalProps) {
  const useFallback = parcelSeries === null || parcelSeries.length === 0

  // Hooks live at the top level (rules of hooks). They run regardless of
  // which branch we render, but their effects only matter in the path
  // they describe.
  useEffect(() => {
    if (useFallback) {
      logCortex("info", "fallback: parcel_series unavailable; rendering placeholder", {
        reason: parcelSeries === null ? "null" : "empty",
      })
    } else if (parcelSeries) {
      logCortex("info", "mounted with parcelSeries", {
        n_trs: parcelSeries.length,
        n_parcels: parcelSeries[0]?.length ?? 0,
        variant,
      })
    }
  }, [useFallback, parcelSeries, variant])

  // ── Fallback path: no parcel data → render the placeholder ─────────────
  // ASSUMPTIONS_BRAIN.md §3.6. The placeholder is a colored icosahedron
  // tinted by the dominant ROI; it's visibly synthetic but never broken.
  if (useFallback || !parcelSeries) {
    return <Brain3D roiValues={roiValues} size={size} />
  }

  const containerStyle = size != null
    ? { width: size, height: size }
    : { width: "100%", height: "100%" }

  return (
    <div style={containerStyle}>
      <Canvas
        camera={{ position: [0, 0, 220], fov: 35 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: "transparent" }}
      >
        <Suspense fallback={null}>
          <CorticalScene
            parcelSeries={parcelSeries}
            tIndex={tIndex}
            variant={variant}
          />
        </Suspense>
      </Canvas>
    </div>
  )
}
