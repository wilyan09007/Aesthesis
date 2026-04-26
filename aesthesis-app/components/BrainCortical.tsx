"use client"

// Cortical brain visualization — fsaverage5 inflated/pial mesh, both
// hemispheres, colored per-face from a pre-baked uint8 RGBA stream.
//
// v4 — neon glass with HDR emissive + bloom (ASSUMPTIONS_BRAIN.md §9.6).
// Builds on v3's per-face shader pattern (uniforms, atlasUV math, alpha
// channel for activation strength) and adds:
//
//   • Front/back mesh split per hemisphere with explicit renderOrder.
//     Back faces render first (darkened), front faces overlay them. No
//     more painter's-algorithm sort glitches on the glass shell.
//   • Smoothstep on uAlpha — TR-to-TR transitions ease in/out instead of
//     stepping linearly, hides the discrete TR cadence.
//   • Fresnel rim glow injected into totalEmissiveRadiance — silhouettes
//     the cortex in cool light, reads as glass volume not paint.
//   • HDR emissive boost driven by per-face activation (extracted from
//     the green channel — neon red has g≈0.08, white-base has g≈0.97).
//     Active patches become self-luminous, feeding the bloom pass.
//   • ACES Filmic tone mapping + UnrealBloomPass — real bloom rather
//     than alpha-faked glow. Threshold is set so only active faces bloom.
//
// React only mounts/unmounts the canvas. Per-frame work is hand-rolled
// three.js — no @react-three/fiber.

import { useEffect, useRef } from "react"
import * as THREE from "three"
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js"
import { OrbitControls } from "three/addons/controls/OrbitControls.js"
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js"
import { RenderPass } from "three/addons/postprocessing/RenderPass.js"
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js"
import { OutputPass } from "three/addons/postprocessing/OutputPass.js"

import type { ROIValues } from "@/lib/types"
import type { AnalyzeResponse } from "@/lib/types"
import Brain3D from "./Brain3D"

export type FaceColors = AnalyzeResponse["timeline"]["face_colors"]
type Hemi = "left" | "right"
export type Variant = "inflated" | "pial"

export interface BrainSceneOptions {
  // Run the EffectComposer + UnrealBloomPass chain. Off for the hero
  // showcase where the canvas is transparent over the page gradients.
  bloom?: boolean
  // OrbitControls auto-rotate. autoRotate spins around the camera's up
  // axis — change cameraUp to spin around a different anatomical axis.
  autoRotate?: boolean
  autoRotateSpeed?: number
  // User input: rotate / zoom / pan. Off for the hero (passive showcase).
  interactive?: boolean
  // Transparent canvas — page background shows through. With transparent=true
  // bloom is disabled regardless of `bloom` (UnrealBloomPass + transparent
  // canvas leaks alpha-premultiplication artifacts into the halo).
  transparent?: boolean
  // Camera framing. Defaults match the results-page brain (up=Y, lateral
  // 3/4 from the upper-left, looking at origin). Hero overrides with
  // up=Z for an anatomical lateral lay-flat view, cameraTarget shifted
  // down so the mesh renders higher in the panel.
  cameraPosition?: [number, number, number]
  cameraUp?: [number, number, number]
  cameraTarget?: [number, number, number]
}

const LEFT_INFLATED = "/brain/fsaverage5-left-inflated.glb"
const RIGHT_INFLATED = "/brain/fsaverage5-right-inflated.glb"
const LEFT_PIAL = "/brain/fsaverage5-left-pial.glb"
const RIGHT_PIAL = "/brain/fsaverage5-right-pial.glb"

const FACES_PER_HEMI = 20480

// Matches --bg in app/globals.css. The canvas paints opaque so the bloom
// pass has a clean substrate; visually indistinguishable from transparent
// over the same body bg because the panel covers nothing else here.
const SCENE_CLEAR = 0x0b0f14

interface BrainCorticalProps {
  parcelSeries: number[][] | null
  faceColors: FaceColors
  tIndex: number
  currentTime: number
  trDurationS: number
  roiValues?: ROIValues
  variant?: Variant
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
    padded.set(bytes.subarray(0, total * 4))
  } else {
    // Legacy uint8_rgb_bin — pad to RGBA with full opacity. Old workers
    // don't ship the activation-driven alpha, so they render as solid
    // shells. Bloom still kicks in on red dominance.
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
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  return { tex, w, h, nFrames, channels: ch }
}

// ─── Shader patches ─────────────────────────────────────────────────────────

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
uniform float uEmissiveBoost;
uniform float uFresnelStrength;
uniform float uFresnelPower;
uniform vec3  uFresnelTint;
uniform float uBackDim;

flat varying float vFaceIndex;

vec2 atlasUV(float face, float frame) {
  float f = clamp(face, 0.0, uNumFaces - 1.0);
  float idx = frame * uNumFaces + f;
  float x = mod(idx, uAtlasW);
  float y = floor(idx / uAtlasW);
  return vec2((x + 0.5) / uAtlasW, (y + 0.5) / uAtlasH);
}
`

// Replaces <map_fragment>. Sets diffuseColor (RGB + per-fragment alpha)
// from the atlas with smoothstep'd temporal interpolation. uBackDim
// (1.0 for the front mesh, ~0.55 for the back mesh) attenuates back-face
// contribution so glass depth reads cleanly.
const FRAG_MAP = `
vec4 c0 = texture2D(uFaceTex, atlasUV(vFaceIndex, uFrame0));
vec4 c1 = texture2D(uFaceTex, atlasUV(vFaceIndex, uFrame1));
float ta = smoothstep(0.0, 1.0, clamp(uAlpha, 0.0, 1.0));
vec4 finalColor = mix(c0, c1, ta);

// Mild toe-lift keeps mid-zone reds from washing out under tone-mapping.
finalColor.rgb = pow(finalColor.rgb, vec3(1.05));
finalColor.rgb = max(finalColor.rgb, vec3(0.012));

// Missing-data fallback — only override RGB, keep alpha.
float rgbSum = finalColor.r + finalColor.g + finalColor.b;
if (rgbSum < 0.001) {
  finalColor.rgb = vec3(0.045);
}

// Backside attenuation — rendered in pass 1 (renderOrder 0); the front
// mesh in pass 2 (renderOrder 1) keeps full intensity.
finalColor.rgb *= uBackDim;
finalColor.a   *= mix(1.0, 0.55, 1.0 - uBackDim);

diffuseColor.rgba = finalColor;
`

// Replaces <emissivemap_fragment>. Adds:
//   • activation-driven emission (red dominance ⇒ self-luminous neon)
//   • fresnel rim emission (cool tint at the silhouette)
// Both flow into totalEmissiveRadiance, which the lighting pipeline adds
// to the final fragment color and which UnrealBloomPass then samples.
const FRAG_EMISSIVE = `#include <emissivemap_fragment>
{
  // White-base has g ≈ 0.97; neon red has g ≈ 0.08. (1 - g) is a clean
  // single-tail "redness" signal — nothing extra in the wire format.
  float activation = clamp((1.0 - diffuseColor.g - 0.04) * 1.20, 0.0, 1.0);

  vec3 V = normalize(vViewPosition);
  vec3 N = normalize(normal);
  float fr = pow(1.0 - clamp(dot(V, N), 0.0, 1.0), uFresnelPower);

  // Activation glow tracks the diffuse color so neon reds stay neon.
  totalEmissiveRadiance += diffuseColor.rgb * activation * uEmissiveBoost;
  // Cool-tinted rim — silhouette without recolouring the bulk of the cortex.
  totalEmissiveRadiance += uFresnelTint * fr * uFresnelStrength;
}
`

interface FaceMaterialUniforms {
  uFaceTex: { value: THREE.Texture | null }
  uAtlasW: { value: number }
  uAtlasH: { value: number }
  uNumFaces: { value: number }
  uFrame0: { value: number }
  uFrame1: { value: number }
  uAlpha: { value: number }
  uEmissiveBoost: { value: number }
  uFresnelStrength: { value: number }
  uFresnelPower: { value: number }
  uFresnelTint: { value: THREE.Color }
  uBackDim: { value: number }
}

function makeFaceMaterial(
  uniforms: FaceMaterialUniforms,
  side: THREE.Side,
): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.78,
    metalness: 0.04,
    side,
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
      .replace("#include <emissivemap_fragment>", FRAG_EMISSIVE)
  }
  return mat
}

// ─── Geometry post-processing ───────────────────────────────────────────────

function prepareFaceGeometry(geom: THREE.BufferGeometry): THREE.BufferGeometry {
  const indexed = geom.clone()
  // The bake (scripts/bake_brain_glbs.py) ships pre-computed NORMAL,
  // smoothed across the indexed pial topology. Re-using them avoids the
  // ~50ms per-hemi computeVertexNormals cost AND is exactly what the
  // bake intended (pial gyri/sulci read as anatomy under directional lights).
  // Only fall back to computing if the GLB was authored without normals.
  if (!indexed.attributes.normal) {
    indexed.computeVertexNormals()
  }
  const ng = indexed.index ? indexed.toNonIndexed() : indexed
  const n = ng.attributes.position.count
  const aFace = new Float32Array(n)
  const nFaces = Math.floor(n / 3)
  for (let i = 0; i < nFaces; i++) {
    const v = i * 3
    aFace[v] = i
    aFace[v + 1] = i
    aFace[v + 2] = i
  }
  ng.setAttribute("aFace", new THREE.BufferAttribute(aFace, 1))
  return ng
}

// ─── Scene class ────────────────────────────────────────────────────────────

interface HemiPair {
  geom: THREE.BufferGeometry
  back: THREE.Mesh
  front: THREE.Mesh
  matBack: THREE.MeshStandardMaterial
  matFront: THREE.MeshStandardMaterial
  uniformsFront: FaceMaterialUniforms
  uniformsBack: FaceMaterialUniforms
  texture: THREE.DataTexture
  nFrames: number
}

export class BrainScene {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  composer: EffectComposer | null
  bloom: UnrealBloomPass | null
  useBloom: boolean
  hemis: { left: HemiPair | null; right: HemiPair | null } = { left: null, right: null }
  raf = 0

  constructor(canvas: HTMLCanvasElement, opts: BrainSceneOptions = {}) {
    const transparent = opts.transparent ?? false
    // Bloom + transparent canvas leak alpha-premultiplication artifacts
    // into the halo, so disable bloom whenever the canvas is transparent.
    this.useBloom = (opts.bloom ?? true) && !transparent
    const interactive = opts.interactive ?? true
    const autoRotate = opts.autoRotate ?? false
    const autoRotateSpeed = opts.autoRotateSpeed ?? 0.6

    this.scene = new THREE.Scene()
    this.scene.background = transparent ? null : new THREE.Color(SCENE_CLEAR)

    this.camera = new THREE.PerspectiveCamera(28, 1, 0.1, 2000)
    // camera.up MUST be set BEFORE OrbitControls is constructed — the
    // controls cache the up vector and use it as the spherical pole.
    // Auto-rotate then spins around this axis.
    //
    // Defaults: anatomical lateral 3/4 view of the LEFT hemisphere with
    // the superior axis (Z in MNI/freesurfer) as screen-up. AP axis lies
    // horizontally, IS axis vertical — the canonical anatomy-textbook
    // orientation for both the hero showcase and the results panel.
    const [ux, uy, uz] = opts.cameraUp ?? [0, 0, 1]
    this.camera.up.set(ux, uy, uz)
    const [px, py, pz] = opts.cameraPosition ?? [-320, 92, 95]
    this.camera.position.set(px, py, pz)
    const [tx, ty, tz] = opts.cameraTarget ?? [0, 0, 0]
    this.camera.lookAt(tx, ty, tz)

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: transparent,
      premultipliedAlpha: !transparent,
      powerPreference: "high-performance",
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    if (transparent) {
      this.renderer.setClearColor(0x000000, 0)
    } else {
      this.renderer.setClearColor(SCENE_CLEAR, 1)
    }
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.05
    this.renderer.outputColorSpace = THREE.SRGBColorSpace

    this.scene.add(new THREE.AmbientLight(0xc8d4ff, 0.55))
    const key = new THREE.DirectionalLight(0xfff4e6, 0.85)
    key.position.set(-220, 220, 260)
    this.scene.add(key)
    const fill = new THREE.DirectionalLight(0x99b4ff, 0.35)
    fill.position.set(180, -80, -200)
    this.scene.add(fill)
    const rim = new THREE.DirectionalLight(0xb8c8ff, 0.45)
    rim.position.set(0, -140, -300)
    this.scene.add(rim)

    this.controls = new OrbitControls(this.camera, canvas)
    // OrbitControls orbits around `.target`, NOT around the camera's
    // current lookAt. Match it to cameraTarget so auto-rotate spins
    // around the user's intended pivot.
    this.controls.target.set(tx, ty, tz)
    this.controls.enableRotate = interactive
    this.controls.rotateSpeed = 1.0
    this.controls.enableZoom = interactive
    this.controls.zoomSpeed = 1.2
    this.controls.zoomToCursor = true
    this.controls.minDistance = 80
    this.controls.maxDistance = 700
    this.controls.enablePan = false
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.1
    this.controls.autoRotate = autoRotate
    // OrbitControls 0.6 ≈ 100s/orbit at 60fps — slow, contemplative
    // showcase pace. autoRotate continues to spin even when the user
    // can't drag (interactive=false) — they're independent.
    this.controls.autoRotateSpeed = autoRotateSpeed

    if (this.useBloom) {
      this.composer = new EffectComposer(this.renderer)
      this.composer.addPass(new RenderPass(this.scene, this.camera))
      this.bloom = new UnrealBloomPass(
        new THREE.Vector2(canvas.width, canvas.height),
        0.62,
        0.5,
        1.0,
      )
      this.composer.addPass(this.bloom)
      this.composer.addPass(new OutputPass())
    } else {
      this.composer = null
      this.bloom = null
    }

    const animate = () => {
      this.raf = requestAnimationFrame(animate)
      this.controls.update()
      if (this.composer) {
        this.composer.render()
      } else {
        this.renderer.render(this.scene, this.camera)
      }
    }
    animate()
  }

  resize(w: number, h: number) {
    this.renderer.setSize(w, h, false)
    this.composer?.setSize(w, h)
    this.bloom?.setSize(w, h)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }

  async loadHemispheres(variant: Variant, colors: FaceColors): Promise<void> {
    if (!colors) return
    const loader = new GLTFLoader()
    const leftUrl = variant === "inflated" ? LEFT_INFLATED : LEFT_PIAL
    const rightUrl = variant === "inflated" ? RIGHT_INFLATED : RIGHT_PIAL

    const [leftGlb, rightGlb] = await Promise.all([
      loader.loadAsync(leftUrl),
      loader.loadAsync(rightUrl),
    ])
    logBC("info", "GLBs loaded", { variant, left: leftUrl, right: rightUrl })

    this.hemis = {
      left: this.bindHemisphere("left", leftGlb, colors.left),
      right: this.bindHemisphere("right", rightGlb, colors.right),
    }
  }

  private bindHemisphere(
    hemi: Hemi,
    gltf: { scene: THREE.Group },
    payload: NonNullable<FaceColors>["left"],
  ): HemiPair {
    let baseMesh: THREE.Mesh | null = null
    gltf.scene.traverse((obj) => {
      if (!baseMesh && obj instanceof THREE.Mesh) baseMesh = obj
    })
    if (!baseMesh) throw new Error(`${hemi}: no Mesh found in GLB`)

    const m: THREE.Mesh = baseMesh
    const geom = prepareFaceGeometry(m.geometry as THREE.BufferGeometry)

    const bytes = decodeBase64ToU8(payload.data_b64)
    const nFrames = payload.n_frames
    const nFaces = payload.n_faces
    if (nFaces !== FACES_PER_HEMI) {
      logBC("warn", `${hemi}: face count ${nFaces} != ${FACES_PER_HEMI}`)
    }
    const { tex, w, h, channels } = buildAtlasTexture(bytes, nFrames, nFaces)
    logBC("info", `${hemi}: atlas built`, {
      w, h, nFrames, nFaces, bytes: bytes.length, channels, format: payload.format,
    })

    // Two materials per hemi share the SAME texture + scalar uniforms,
    // but each material gets its own copy of the uniform refs (so
    // onBeforeCompile sees them as live). Time uniforms are written by
    // setTime through both objects.
    const fresnelTint = new THREE.Color(0x86a8ff)
    const baseUniforms = (uBackDim: number): FaceMaterialUniforms => ({
      uFaceTex: { value: tex },
      uAtlasW: { value: w },
      uAtlasH: { value: h },
      uNumFaces: { value: nFaces },
      uFrame0: { value: 0 },
      uFrame1: { value: 0 },
      uAlpha: { value: 0 },
      uEmissiveBoost: { value: 1.6 },
      uFresnelStrength: { value: 1.4 },
      uFresnelPower: { value: 2.4 },
      uFresnelTint: { value: fresnelTint.clone() },
      uBackDim: { value: uBackDim },
    })

    const uniformsFront = baseUniforms(1.0)
    const uniformsBack = baseUniforms(0.55)

    const matFront = makeFaceMaterial(uniformsFront, THREE.FrontSide)
    const matBack = makeFaceMaterial(uniformsBack, THREE.BackSide)

    const back = new THREE.Mesh(geom, matBack)
    back.renderOrder = 0
    const front = new THREE.Mesh(geom, matFront)
    front.renderOrder = 1

    this.scene.add(back)
    this.scene.add(front)

    return {
      geom,
      back,
      front,
      matBack,
      matFront,
      uniformsFront,
      uniformsBack,
      texture: tex,
      nFrames,
    }
  }

  setTime(currentTime: number, trDurationS: number): void {
    const tr = trDurationS || 1.5
    for (const hemi of ["left", "right"] as const) {
      const pair = this.hemis[hemi]
      if (!pair) continue
      const max = Math.max(0, pair.nFrames - 1)
      const raw = currentTime / tr
      const f0 = Math.max(0, Math.min(max, Math.floor(raw)))
      const f1 = Math.max(0, Math.min(max, f0 + 1))
      const alpha = Math.max(0, Math.min(1, raw - Math.floor(raw)))
      pair.uniformsFront.uFrame0.value = f0
      pair.uniformsFront.uFrame1.value = f1
      pair.uniformsFront.uAlpha.value = alpha
      pair.uniformsBack.uFrame0.value = f0
      pair.uniformsBack.uFrame1.value = f1
      pair.uniformsBack.uAlpha.value = alpha
    }
  }

  dispose(): void {
    cancelAnimationFrame(this.raf)
    this.controls.dispose()
    for (const hemi of ["left", "right"] as const) {
      const pair = this.hemis[hemi]
      if (!pair) continue
      this.scene.remove(pair.back)
      this.scene.remove(pair.front)
      pair.geom.dispose()
      pair.matBack.dispose()
      pair.matFront.dispose()
      pair.texture.dispose()
    }
    this.bloom?.dispose()
    this.composer?.dispose()
    this.renderer.dispose()
  }
}

// ─── React wrapper ──────────────────────────────────────────────────────────

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

    const c = containerRef.current!
    sc.resize(c.clientWidth, c.clientHeight)

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

  // Subtle up-right shift on the rendered output. Applied post-projection
  // so OrbitControls' rotation pivot doesn't drift across the orbit
  // (a world-space target / camera offset would oscillate the visual
  // offset as the user rotates).
  const containerStyle = size != null
    ? { width: size, height: size, transform: "translate(20px, -20px)" }
    : { width: "100%", height: "100%", transform: "translate(20px, -20px)" }

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
