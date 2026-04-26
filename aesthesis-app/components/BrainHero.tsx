"use client"

// Hero-page brain showcase — same fsaverage5 cortical mesh, same v4
// shader, driven by a synthetic activation sequence baked client-side
// from per-face 3D centroids loaded from the GLB. Spins slowly via
// OrbitControls.autoRotate, non-interactive, transparent canvas so
// the hero's radial gradients and decorative rings show through.
//
// The synthetic data is event-based: small contiguous patches anchored
// to randomly-chosen 3D seed points light up, peak, and decay. Far
// more believable than the earlier face-index pattern, which scattered
// triangles because freesurfer's tessellation order isn't strongly
// index-coherent.

import { useEffect, useRef, useState } from "react"
import { BrainScene } from "./BrainCortical"
import type { FaceColors } from "./BrainCortical"
import { buildSyntheticFromCentroids, getFaceCentroids } from "@/lib/brainSynthetic"

interface BrainHeroProps {
  size?: number
}

const N_FRAMES = 60
const TR_S = 0.25 // 60 * 0.25 = 15 s per loop

export default function BrainHero({ size = 400 }: BrainHeroProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  // faceColors is computed asynchronously: we have to wait for the GLB
  // (for the per-face 3D centroids) before we can bake the synthetic
  // activation atlas. Stays null until both are ready, at which point
  // the second useEffect mounts the scene.
  const [faceColors, setFaceColors] = useState<FaceColors>(null)

  useEffect(() => {
    let cancelled = false
    // Fresh seeds per page load so the activation pattern varies
    // session-to-session — same showcase, never quite the same brain.
    const seedLeft = (Math.random() * 0xffffffff) >>> 0
    const seedRight = (Math.random() * 0xffffffff) >>> 0
    ;(async () => {
      const centroids = await getFaceCentroids()
      if (cancelled) return
      const fc = buildSyntheticFromCentroids(centroids, {
        nFrames: N_FRAMES,
        eventsPerHemi: 8,
        seedLeft,
        seedRight,
      })
      if (!cancelled) setFaceColors(fc)
    })().catch((err) => {
      // eslint-disable-next-line no-console
      console.error("[brain-hero] failed to bake synthetic activations", err)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!faceColors) return
    const canvas = canvasRef.current
    if (!canvas) return

    const sc = new BrainScene(canvas, {
      bloom: false,            // bloom + transparent leak halos; skip
      autoRotate: true,
      autoRotateSpeed: 0.55,   // ≈110s per orbit — slow showcase pace
      interactive: false,      // passive; users don't grab the hero brain
      transparent: true,
      // Push camera further from origin than the BrainScene default —
      // the hero panel benefits from a touch more breathing room around
      // the cortex (results-panel default is ~302, hero uses ~395).
      // Up axis and look-at target inherit the BrainScene defaults
      // ([0,0,1] and origin), giving the same horizontal lateral 3/4
      // framing as the results brain.
      cameraPosition: [-365, 105, 110],
    })

    const c = containerRef.current!
    sc.resize(c.clientWidth, c.clientHeight)

    const ro = new ResizeObserver(() => {
      const w = c.clientWidth
      const h = c.clientHeight
      if (w > 0 && h > 0) sc.resize(w, h)
    })
    ro.observe(c)

    sc.loadHemispheres("pial", faceColors).catch((err) => {
      // eslint-disable-next-line no-console
      console.error("[brain-hero] failed to load hemispheres", err)
    })

    // Internal time clock — RAF-driven, no React re-renders. setTime
    // clamps via per-hemi nFrames so out-of-range time can't paint
    // garbage during the loop wraparound.
    const loopDuration = N_FRAMES * TR_S
    let raf = 0
    let start = 0
    const tick = (now: number) => {
      if (!start) start = now
      const t = ((now - start) / 1000) % loopDuration
      sc.setTime(t, TR_S)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      sc.dispose()
    }
  }, [faceColors])

  return (
    // Tiny CSS shift left — applied post-projection so the offset stays
    // stable as auto-rotate orbits the camera around the brain's vertical
    // axis. (A world-space camera/target/scene shift would oscillate
    // left↔right across the orbit instead of holding constant.)
    <div
      ref={containerRef}
      style={{ width: size, height: size, transform: "translate(-12px, 0)" }}
    >
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />
    </div>
  )
}
