"use client"

import { useRef, useMemo } from "react"
import { Canvas, useFrame } from "@react-three/fiber"
import { OrbitControls } from "@react-three/drei"
import * as THREE from "three"
import type { ROIValues } from "@/lib/types"

interface Brain3DProps {
  roiValues?: ROIValues
  /** Fixed pixel size. If omitted, the canvas fills its parent. */
  size?: number
}

// ROI values arrive z-scored (TRIBE step2_roi.py:128). Squash through tanh
// so ±2σ saturates at ±1 and z=0 maps cleanly to the neutral midpoint.
// Without this, the previous additive mapping ignored sign — negative
// activations darkened the brain instead of distinguishing themselves
// from positive ones, making "low friction" and "high cognitive load"
// look identical (both dim).
function smooth(z: number): number {
  return Math.tanh(z * 0.7)
}

// Diverging mapping: each ROI shifts the brain from a calm baseline blue
// toward its own warm hue when *positive*, and toward a cooler shade when
// *negative*. The product story is "live brain reading" — a static dim
// blob can't tell that story.
function computeTargetColor(roi?: ROIValues): { color: THREE.Color; emissive: THREE.Color } {
  // Calm baseline: muted indigo. The "brain at rest" color.
  const base = { r: 0.16, g: 0.24, b: 0.48 }

  if (!roi) {
    return {
      color: new THREE.Color(base.r, base.g, base.b),
      emissive: new THREE.Color(base.r * 0.3, base.g * 0.3, base.b * 0.55),
    }
  }

  const friction = smooth(roi.friction_anxiety)
  const rewardTrust = smooth((roi.reward_anticipation + roi.trust_affinity) / 2)
  const cognitive = smooth(roi.cognitive_load)

  // Each ROI pushes its color channel up when positive and pulls it down
  // when negative, with smaller pull than push (asymmetric: activations
  // are more visually salient than de-activations, which is also true
  // perceptually for the user).
  const r = base.r + (friction > 0 ? friction * 0.50 : friction * 0.10)
  const g = base.g + (rewardTrust > 0 ? rewardTrust * 0.50 : rewardTrust * 0.10)
  const b = base.b + (cognitive > 0 ? cognitive * 0.40 : cognitive * 0.10)

  return {
    color: new THREE.Color(
      Math.max(0.05, Math.min(1, r)),
      Math.max(0.05, Math.min(1, g)),
      Math.max(0.05, Math.min(1, b)),
    ),
    emissive: new THREE.Color(r * 0.3, g * 0.3, b * 0.4),
  }
}

interface BrainMeshProps {
  roiValues?: ROIValues
}

function BrainMesh({ roiValues }: BrainMeshProps) {
  const meshRef = useRef<THREE.Mesh>(null)
  const innerRef = useRef<THREE.Mesh>(null)
  const matRef = useRef<THREE.MeshStandardMaterial>(null)
  const innerMatRef = useRef<THREE.MeshStandardMaterial>(null)
  const currentColor = useRef(new THREE.Color(0.18, 0.28, 0.55))
  const currentEmissive = useRef(new THREE.Color(0.05, 0.1, 0.28))

  const geometry = useMemo(() => {
    // detail=4 (5120 faces) is needed so the gyri displacement reads as
    // crinkled folds rather than coarse facets.
    const geo = new THREE.IcosahedronGeometry(1.4, 4)
    const pos = geo.attributes.position
    const v = new THREE.Vector3()

    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i)

      // 1) Brain proportions — slightly narrower side-to-side, longer
      //    front-to-back. Bottom half is flatter than the top so the
      //    cerebrum domes up the way a real brain sits on its base.
      v.x *= 0.95
      v.y *= v.y > 0 ? 0.95 : 0.65
      v.z *= 1.20

      // 2) Cerebellum (bilateral bumps at the back-bottom) + brainstem
      //    (single small protrusion below the cerebellum). Gaussians
      //    centered at the anatomical positions; widths set so the
      //    bumps stay localized.
      const cerebL = Math.exp(-((v.x + 0.4) ** 2 * 6 + (v.y + 0.45) ** 2 * 4 + (v.z + 0.75) ** 2 * 4))
      const cerebR = Math.exp(-((v.x - 0.4) ** 2 * 6 + (v.y + 0.45) ** 2 * 4 + (v.z + 0.75) ** 2 * 4))
      const stem   = Math.exp(-(v.x ** 2 * 14 + (v.y + 0.85) ** 2 * 7 + (v.z + 0.30) ** 2 * 5))
      const bumps  = 0.20 * (cerebL + cerebR) + 0.14 * stem

      // 3) Gyri — layered sines for the crinkly cerebral-cortex surface.
      const gyri =
        0.07 * Math.sin(v.x * 6.5 + v.z * 5.3) * Math.cos(v.y * 5.5) +
        0.05 * Math.sin(v.y * 7.7 + v.x * 4.2) * Math.cos(v.z * 7.5) +
        0.03 * Math.sin(v.z * 9.3 + v.y * 5.7)

      // Push outward along the surface normal by bumps + gyri.
      let dir = v.clone().normalize()
      v.copy(dir.multiplyScalar(v.length() + bumps + gyri))

      // 4) Longitudinal fissure — the deep cleft splitting left/right
      //    hemispheres. Apply *after* the bumps so it actually cuts
      //    through the cerebrum surface (not just before it). Pull
      //    inward along the (new) normal where the vertex is near the
      //    centerline, on the top half.
      const fissCenter = Math.exp(-v.x * v.x * 18)            // narrow X band
      const fissZ      = Math.exp(-v.z * v.z * 0.3)           // mild front-back taper
      const fissY      = Math.max(0, (v.y + 0.1) / 1.0)       // mostly top, slight wrap
      const fissure    = 0.32 * fissCenter * fissZ * fissY

      dir = v.clone().normalize()
      v.copy(dir.multiplyScalar(v.length() - fissure))

      pos.setXYZ(i, v.x, v.y, v.z)
    }
    geo.computeVertexNormals()
    return geo
  }, [])

  useFrame((state) => {
    if (!meshRef.current || !matRef.current || !innerRef.current || !innerMatRef.current) return

    const t = state.clock.elapsedTime
    meshRef.current.rotation.y += 0.004
    meshRef.current.rotation.x = Math.sin(t * 0.25) * 0.12

    innerRef.current.rotation.y -= 0.003
    innerRef.current.rotation.z = Math.cos(t * 0.18) * 0.08

    const { color: target, emissive: targetEmissive } = computeTargetColor(roiValues)

    if (!roiValues) {
      const cycle = (Math.sin(t * 0.4) + 1) / 2
      target.lerp(new THREE.Color(0.12, 0.38, 0.32), cycle)
    }

    currentColor.current.lerp(target, 0.04)
    currentEmissive.current.lerp(targetEmissive, 0.04)

    matRef.current.color.copy(currentColor.current)
    matRef.current.emissive.copy(currentEmissive.current)
    innerMatRef.current.emissive.copy(currentEmissive.current)
    innerMatRef.current.emissiveIntensity = 0.4 + Math.sin(t * 0.7) * 0.15
  })

  return (
    <group>
      {/* Core mesh — opaque so the gyri shadows actually read as folds. */}
      <mesh ref={meshRef} geometry={geometry} castShadow>
        <meshStandardMaterial
          ref={matRef}
          color={new THREE.Color(0.18, 0.28, 0.55)}
          emissive={new THREE.Color(0.05, 0.1, 0.28)}
          emissiveIntensity={0.45}
          roughness={0.78}
          metalness={0.05}
          flatShading={false}
        />
      </mesh>

      {/* Inner glow sphere */}
      <mesh ref={innerRef} scale={0.7}>
        <sphereGeometry args={[1.5, 16, 12]} />
        <meshStandardMaterial
          ref={innerMatRef}
          color={new THREE.Color(0, 0, 0)}
          emissive={new THREE.Color(0.05, 0.1, 0.28)}
          emissiveIntensity={0.4}
          transparent
          opacity={0.25}
          side={THREE.BackSide}
        />
      </mesh>

      {/* Point light for glow */}
      <pointLight color="#E0454D" intensity={1.5} distance={6} />
    </group>
  )
}

export default function Brain3D({ roiValues, size }: Brain3DProps) {
  // When size is given, render a fixed square. Otherwise fill the parent so
  // a parent panel (e.g. the Neural-state card in ResultsView) can drive
  // sizing via vh/percent without us depending on `window` (which would
  // SSR-fail in Next.js).
  const containerStyle = size != null
    ? { width: size, height: size }
    : { width: "100%", height: "100%" }
  return (
    <div style={containerStyle}>
      <Canvas
        camera={{ position: [0, 0, 4.5], fov: 40 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: "transparent" }}
      >
        {/* Lower ambient + higher-contrast key light = gyri shadows that
            actually read as folds rather than smooth bumps. */}
        <ambientLight intensity={0.12} />
        <directionalLight position={[3, 4, 3]} intensity={0.95} color="#a0b8ff" />
        <directionalLight position={[-3, -2, -3]} intensity={0.25} color="#E0454D" />
        <BrainMesh roiValues={roiValues} />
        {/* Rotate is the interaction that makes the brain feel alive. Pan/zoom
            are intentionally off — they'd let the user lose the brain off-screen
            with no easy recovery. This sets up cleanly for the niivue/GLB
            swap (see implementation plan): same controls, real geometry. */}
        <OrbitControls enableZoom={false} enablePan={false} enableRotate />
      </Canvas>
    </div>
  )
}
