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
    const geo = new THREE.IcosahedronGeometry(1.5, 2)
    const pos = geo.attributes.position
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i)
      const y = pos.getY(i)
      const z = pos.getZ(i)
      const noise = 0.1 * Math.sin(x * 3.1 + y * 2.3) * Math.cos(y * 2.7 + z * 1.9) + 0.05 * Math.sin(z * 4.1 + x * 1.7)
      const len = Math.sqrt(x * x + y * y + z * z)
      const scale = (len + noise) / len
      pos.setXYZ(i, x * scale, y * scale, z * scale)
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
      {/* Core mesh */}
      <mesh ref={meshRef} geometry={geometry} castShadow>
        <meshStandardMaterial
          ref={matRef}
          color={new THREE.Color(0.18, 0.28, 0.55)}
          emissive={new THREE.Color(0.05, 0.1, 0.28)}
          emissiveIntensity={0.5}
          transparent
          opacity={0.82}
          roughness={0.65}
          metalness={0.1}
          wireframe={false}
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
      <pointLight color="#7C9CFF" intensity={1.5} distance={6} />
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
        <ambientLight intensity={0.2} />
        <directionalLight position={[3, 3, 3]} intensity={0.6} color="#a0b8ff" />
        <directionalLight position={[-3, -2, -3]} intensity={0.3} color="#5CF2C5" />
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