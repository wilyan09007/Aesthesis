"use client"

// Real cortical brain visualization — fsaverage5 inflated mesh, both
// hemispheres, colored per-face from a pre-baked uint8 RGB stream.
//
// IMPLEMENTATION NOTE: This component is hand-rolled three.js, not
// @react-three/fiber. We're matching Meta's TRIBE v2 demo bit-for-bit
// (bundle reverse-engineered in ASSUMPTIONS_BRAIN.md §7.0). Meta uses:
//   - Custom GLSL shader injected via MeshStandardMaterial.onBeforeCompile
//   - Per-face IDs as a vertex attribute (`aFace`) after un-indexing
//   - DataTexture atlas of (n_TRs * n_faces, 3) uint8 RGB
//   - Temporal interpolation (uFrame0 + uFrame1 + uAlpha) for smooth
//     transitions between TRs
//
// The vertex/fragment shader patches below are byte-equivalent to the
// strings extracted from Meta's BrainViewer-15466291.js bundle. Same
// uniforms, same atlasUV math, same toe-lift / black-floor / missing-
// data handling.

import { useEffect, useRef } from "react"
import * as THREE from "three"
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js"
import { OrbitControls } from "three/addons/controls/OrbitControls.js"

import type { ROIValues } from "@/lib/types"
import type { AnalyzeResponse } from "@/lib/types"
import Brain3D from "./Brain3D"

type FaceColors = AnalyzeResponse["timeline"]["face_colors"]
type Hemi = "left" | "right"

const LEFT_INFLATED = "/brain/fsaverage5-left-inflated.glb"
const RIGHT_INFLATED = "/brain/fsaverage5-right-inflated.glb"
const LEFT_PIAL = "/brain/fsaverage5-left-pial.glb"
const RIGHT_PIAL = "/brain/fsaverage5-right-pial.glb"

const FACES_PER_HEMI = 20480

interface BrainCorticalProps {
  /** (n_TRs, 400) parcel z-scores. Fallback/debug only — face_colors is the renderer. */
  parcelSeries: number[][] | null
  /** Per-face uint8 RGB stream from the TRIBE worker. Null → fall back to placeholder. */
  faceColors: FaceColors
  /** Floor(currentTime / tr_duration_s) clamped to the valid range. */
  tIndex: number
  /** Continuous time in seconds, used to interpolate between TRs (uAlpha). */
  currentTime: number
  trDurationS: number
  /** ROI values consumed by the placeholder fallback only. */
  roiValues?: ROIValues
  /**
   * Surface type. "pial" = realistic gyri/sulci anatomy (matches Meta's
   * "Normal" tab and screenshot). "inflated" = smoothed bubble that
   * exposes sulcal interiors. Default "pial" for the realistic look.
   */
  variant?: "inflated" | "pial"
  size?: number
}

function logBC(level: "info" | "warn" | "error", msg: string, extra?: unknown) {
  // eslint-disable-next-line no-console
  console[level](`[brain-cortical] ${msg}`, extra ?? "")
}

// ─── Atlas builder ──────────────────────────────────────────────────────────

const MAX_ATLAS_W = 4096

function decodeBase64ToU8(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

/**
 * Pack an RGBA (or legacy RGB) byte stream into a 2D atlas DataTexture.
 *
 * Atlas layout: linear index = frame * n_faces + face, then row-major
 * into a 2D texture with width capped at MAX_ATLAS_W. The shader's
 * atlasUV() function does the inverse mapping.
 *
 * Format auto-detected from byte count vs (nFrames * nFaces). RGBA
 * data lands as-is; RGB is expanded to RGBA with alpha=255. The
 * texture itself is always RGBA (three.js r165+ removed RGBFormat).
 */
function buildAtlasTexture(
  bytes: Uint8Array,
  nFrames: number,
  nFaces: number,
): { tex: THREE.DataTexture; w: number; h: number; nFrames: number; channels: 3 | 4 } {
  const total = nFrames * nFaces
  const ch: 3 | 4 = bytes.length === total * 4 ? 4 : 3
  const w = Math.min(MAX_ATLAS_W, Math.max(64, Math.ceil(Math.sqrt(total))))
  const h = Math.ceil(total / w)
  const need = w * h * 4
  const padded = new Uint8Array(need)
  if (ch === 4) {
    // RGBA stream — copy as-is (alpha already encodes the activation
    // strength from the bake; shader writes it to diffuseColor.a).
    padded.set(bytes.subarray(0, total * 4))
  } else {
    // Legacy RGB stream — expand to RGBA with full opacity.
    for (let i = 0; i < total; i++) {
      padded[i * 4 + 0] = bytes[i * 3 + 0]
      padded[i * 4 + 1] = bytes[i * 3 + 1]
      padded[i * 4 + 2] = bytes[i * 3 + 2]
      padded[i * 4 + 3] = 255
    }
  }
  const tex = new THREE.DataTexture(padded, w, h, THREE.RGBAFormat, THREE.UnsignedByteType)
  tex.minFilter = THREE.NearestFilter
  tex.magFilter = THREE.NearestFilter
  tex.wrapS = THREE.ClampToEdgeWrapping
  tex.wrapT = THREE.ClampToEdgeWrapping
  tex.generateMipmaps = false
  tex.needsUpdate = true
  return { tex, w, h, nFrames, channels: ch }
}

// ─── Shader patches (byte-equivalent to Meta's BrainViewer bundle) ──────────

const VERT_COMMON = `#include <common>
attribute float aFace;
flat varying float vFaceIndex;
`
const VERT_BEGIN = `#include <begin_vertex>
vFaceIndex = aFace;
`

const FRAG_COMMON = `#include <common>
uniform sampler2D uFaceTex;
uniform float uAtlasW;
uniform float uAtlasH;
uniform float uNumFaces;
uniform float uFrame0;
uniform float uFrame1;
uniform float uAlpha;

flat varying float vFaceIndex;

vec2 atlasUV(float face, float frame) {
  float f = clamp(face, 0.0, uNumFaces - 1.0);
  float idx = frame * uNumFaces + f;
  float x = mod(idx, uAtlasW);
  float y = floor(idx / uAtlasW);
  return vec2((x + 0.5) / uAtlasW, (y + 0.5) / uAtlasH);
}
`

const FRAG_MAP = `
// Glass-brain shader path (RGBA atlas + transparent material).
// Alpha channel of the atlas encodes activation strength: high alpha
// for active patches (they read as solid color), low alpha for the
// resting shell (it reads as a faint ghost outline). We flow alpha
// into diffuseColor.a so the material's transparent flag controls
// visibility per-fragment.
vec4 c0 = texture2D(uFaceTex, atlasUV(vFaceIndex, uFrame0));
vec4 c1 = texture2D(uFaceTex, atlasUV(vFaceIndex, uFrame1));
vec4 finalColor = mix(c0, c1, uAlpha);

// Slight toe lift (Meta's tuning: keeps contrast on the RGB channels).
finalColor.rgb = pow(finalColor.rgb, vec3(1.05));

// Lower black floor so true shadows stay shadowy.
finalColor.rgb = max(finalColor.rgb, vec3(0.012));

// Missing-data fallback — but only override RGB; keep the texture's
// alpha so the resting shell stays appropriately transparent.
float rgbSum = finalColor.r + finalColor.g + finalColor.b;
if (rgbSum < 0.001) {
  finalColor.rgb = vec3(0.045);
}

diffuseColor.rgba = finalColor;
`

interface FaceMaterialUniforms {
  uFaceTex: { value: THREE.Texture | null }
  uAtlasW: { value: number }
  uAtlasH: { value: number }
  uNumFaces: { value: number }
  uFrame0: { value: number }
  uFrame1: { value: number }
  uAlpha: { value: number }
}

/** Build a MeshStandardMaterial whose shader reads per-face from `uFaceTex`.
 *
 * Glass-brain configuration:
 *   transparent = true       — per-fragment alpha controls visibility
 *   depthWrite  = false      — overlapping faces blend instead of culling
 *   side        = DoubleSide — see both inner and outer surface
 *
 * With ``depthWrite=false`` you can get small ordering artifacts where
 * two transparent faces blend in the wrong order from certain camera
 * angles. For a roughly-convex brain mesh viewed from a single side
 * (which is our common case), the artifacts are minor. If they ever
 * become visible we can split into separate FrontSide + BackSide
 * meshes with explicit renderOrder — three.js community guidance from
 * the Codrops glass tutorial.
 */
function makeFaceMaterial(uniforms: FaceMaterialUniforms): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.85,
    metalness: 0.05,
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
  })
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms)
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", VERT_COMMON)
      .replace("#include <begin_vertex>", VERT_BEGIN)
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", FRAG_COMMON)
      .replace("#include <map_fragment>", FRAG_MAP)
  }
  return mat
}

// ─── Geometry post-processing (un-index + aFace attribute) ──────────────────

/**
 * Un-index a geometry and add `aFace` per-vertex attribute = floor(vIdx/3).
 *
 * Critical for pial: compute SMOOTH per-vertex normals on the indexed
 * mesh first, then call toNonIndexed(). Three.js carries the normal
 * attribute through the un-index, so we end up with smooth shading on
 * the pial cortex (gyri/sulci read as real anatomy under directional
 * lights) AND per-face data via aFace.
 *
 * If we computed normals AFTER un-indexing, every face would have its
 * own three vertices that no other face shares, and the normals would
 * collapse to flat per-face normals — fine on the smooth inflated
 * surface, ugly on pial because the realistic anatomy looks faceted.
 */
function prepareFaceGeometry(geom: THREE.BufferGeometry): THREE.BufferGeometry {
  const indexed = geom.clone()
  // Smooth normals on the indexed mesh — vertex sharing averages face
  // normals into per-vertex normals naturally. Required for pial; safe
  // on inflated.
  indexed.computeVertexNormals()
  const ng = indexed.index ? indexed.toNonIndexed() : indexed
  const n = ng.attributes.position.count // n = 3 * nFaces after un-index
  const aFace = new Float32Array(n)
  const nFaces = Math.floor(n / 3)
  for (let i = 0; i < nFaces; i++) {
    const v = i * 3
    aFace[v] = i
    aFace[v + 1] = i
    aFace[v + 2] = i
  }
  ng.setAttribute("aFace", new THREE.BufferAttribute(aFace, 1))
  // Note: do NOT call computeVertexNormals() again here — that would
  // override the smooth normals we just transferred from the indexed
  // mesh.
  return ng
}

// ─── Main scene class (raw three.js, no React rendering pipeline) ───────────

class BrainScene {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  uniforms: { left: FaceMaterialUniforms; right: FaceMaterialUniforms } | null = null
  meshes: { left: THREE.Mesh | null; right: THREE.Mesh | null } = { left: null, right: null }
  textures: { left: THREE.DataTexture | null; right: THREE.DataTexture | null } = {
    left: null,
    right: null,
  }
  nFrames: { left: number; right: number } = { left: 1, right: 1 }
  raf = 0

  constructor(canvas: HTMLCanvasElement) {
    this.scene = new THREE.Scene()
    this.scene.background = null

    this.camera = new THREE.PerspectiveCamera(28, 1, 0.1, 2000)
    // Lateral 3/4 view of the LEFT hemisphere (matches Meta's demo).
    // Brain is in MNI/freesurfer coordinates: X is L-R (left ≤ 0), Y is
    // A-P, Z is I-S. Camera off to the left and slightly above gives
    // the canonical anatomy-textbook angle.
    this.camera.position.set(-260, 60, 180)
    this.camera.lookAt(0, 0, 0)

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      premultipliedAlpha: false,
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setClearColor(0x000000, 0)

    // Lighting tuned for white-base brain — Meta uses an ambient + soft
    // directional combo so the gyri/sulci shadow gently. We keep the
    // overall scene relatively bright since the diffuseColor coming
    // from our texture is a creamy white at rest.
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.85))
    const key = new THREE.DirectionalLight(0xffffff, 0.55)
    key.position.set(-200, 200, 300) // upper-left key light (anatomy textbook angle)
    this.scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.18)
    fill.position.set(200, -100, -200)
    this.scene.add(fill)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableZoom = false
    this.controls.enablePan = false
    this.controls.enableRotate = true
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08

    const animate = () => {
      this.raf = requestAnimationFrame(animate)
      this.controls.update()
      this.renderer.render(this.scene, this.camera)
    }
    animate()
  }

  resize(w: number, h: number) {
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }

  /** Load both hemispheres' GLBs + bind face-color uniforms. */
  async loadHemispheres(variant: "inflated" | "pial", colors: FaceColors): Promise<void> {
    if (!colors) return
    const loader = new GLTFLoader()
    const leftUrl = variant === "inflated" ? LEFT_INFLATED : LEFT_PIAL
    const rightUrl = variant === "inflated" ? RIGHT_INFLATED : RIGHT_PIAL

    const [leftGlb, rightGlb] = await Promise.all([
      loader.loadAsync(leftUrl),
      loader.loadAsync(rightUrl),
    ])
    logBC("info", "GLBs loaded", { variant, left: leftUrl, right: rightUrl })

    this.uniforms = {
      left: this.bindHemisphere("left", leftGlb, colors.left),
      right: this.bindHemisphere("right", rightGlb, colors.right),
    }
  }

  private bindHemisphere(
    hemi: Hemi,
    gltf: { scene: THREE.Group },
    payload: NonNullable<FaceColors>["left"],
  ): FaceMaterialUniforms {
    let baseMesh: THREE.Mesh | null = null
    gltf.scene.traverse((obj) => {
      if (!baseMesh && obj instanceof THREE.Mesh) baseMesh = obj
    })
    if (!baseMesh) throw new Error(`${hemi}: no Mesh found in GLB`)

    const m: THREE.Mesh = baseMesh
    const geom = prepareFaceGeometry(m.geometry as THREE.BufferGeometry)

    // Build the atlas texture from the base64 stream. New format
    // (uint8_rgba_bin) carries alpha; legacy (uint8_rgb_bin) does not.
    // buildAtlasTexture auto-detects from byte count.
    const bytes = decodeBase64ToU8(payload.data_b64)
    const nFrames = payload.n_frames
    const nFaces = payload.n_faces
    if (nFaces !== FACES_PER_HEMI) {
      logBC("warn", `${hemi}: face count ${nFaces} != ${FACES_PER_HEMI}`)
    }
    const { tex, w, h, channels } = buildAtlasTexture(bytes, nFrames, nFaces)
    this.textures[hemi] = tex
    this.nFrames[hemi] = nFrames
    logBC("info", `${hemi}: atlas built`, {
      w, h, nFrames, nFaces, bytes: bytes.length, channels,
      format: payload.format,
    })

    const uniforms: FaceMaterialUniforms = {
      uFaceTex: { value: tex },
      uAtlasW: { value: w },
      uAtlasH: { value: h },
      uNumFaces: { value: nFaces },
      uFrame0: { value: 0 },
      uFrame1: { value: 0 },
      uAlpha: { value: 0 },
    }
    const mat = makeFaceMaterial(uniforms)
    const newMesh = new THREE.Mesh(geom, mat)
    this.scene.add(newMesh)
    this.meshes[hemi] = newMesh
    return uniforms
  }

  /** Drive uFrame0 / uFrame1 / uAlpha from continuous time. */
  setTime(currentTime: number, trDurationS: number): void {
    if (!this.uniforms) return
    const tr = trDurationS || 1.5
    for (const hemi of ["left", "right"] as const) {
      const u = this.uniforms[hemi]
      const max = Math.max(0, this.nFrames[hemi] - 1)
      const raw = currentTime / tr
      const f0 = Math.max(0, Math.min(max, Math.floor(raw)))
      const f1 = Math.max(0, Math.min(max, f0 + 1))
      const alpha = Math.max(0, Math.min(1, raw - Math.floor(raw)))
      u.uFrame0.value = f0
      u.uFrame1.value = f1
      u.uAlpha.value = alpha
    }
  }

  dispose(): void {
    cancelAnimationFrame(this.raf)
    this.controls.dispose()
    for (const m of [this.meshes.left, this.meshes.right]) {
      if (m) {
        this.scene.remove(m)
        m.geometry.dispose()
        ;(m.material as THREE.Material).dispose()
      }
    }
    for (const t of [this.textures.left, this.textures.right]) {
      if (t) t.dispose()
    }
    this.renderer.dispose()
  }
}

// ─── React wrapper (mounting / unmounting only, no per-frame React) ─────────

export default function BrainCortical({
  parcelSeries,
  faceColors,
  tIndex,
  currentTime,
  trDurationS,
  roiValues,
  variant = "pial",
  size,
}: BrainCorticalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const sceneRef = useRef<BrainScene | null>(null)
  const useFallback = !faceColors

  useEffect(() => {
    if (useFallback) {
      logBC("info", "fallback: face_colors null; rendering placeholder Brain3D", {
        hasParcelSeries: parcelSeries != null,
      })
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return

    const sc = new BrainScene(canvas)
    sceneRef.current = sc

    // Initial size
    const c = containerRef.current!
    sc.resize(c.clientWidth, c.clientHeight)

    // Resize observer
    const ro = new ResizeObserver(() => {
      const w = c.clientWidth
      const h = c.clientHeight
      if (w > 0 && h > 0) sc.resize(w, h)
    })
    ro.observe(c)

    sc.loadHemispheres(variant, faceColors).catch((err) => {
      logBC("error", "failed to load hemispheres", err)
    })

    return () => {
      ro.disconnect()
      sc.dispose()
      sceneRef.current = null
    }
  }, [useFallback, variant, faceColors])

  useEffect(() => {
    sceneRef.current?.setTime(currentTime, trDurationS)
  }, [currentTime, trDurationS, tIndex])

  const containerStyle = size != null
    ? { width: size, height: size }
    : { width: "100%", height: "100%" }

  if (useFallback) {
    return <Brain3D roiValues={roiValues} size={size} />
  }
  return (
    <div ref={containerRef} style={containerStyle}>
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: "100%", display: "block" }}
      />
    </div>
  )
}
