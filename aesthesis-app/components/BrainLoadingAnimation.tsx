"use client"

import { motion } from "framer-motion"

const COLORS = {
  bg: "#000000",
  primary: "#E0454D",
  secondary: "#FF7A82",
  tertiary: "#FFC2C7",
}

// 16 nodes arranged in a brain-like silhouette, layered top-to-bottom.
// Coordinates are in a 400x400 SVG viewBox.
const NODES: Array<{ x: number; y: number }> = [
  // L1 — top of cerebrum
  { x: 180, y: 80 },
  { x: 220, y: 80 },
  // L2
  { x: 145, y: 125 },
  { x: 200, y: 115 },
  { x: 255, y: 125 },
  // L3 — widest band (temporal lobes)
  { x: 100, y: 185 },
  { x: 160, y: 175 },
  { x: 240, y: 175 },
  { x: 300, y: 185 },
  // L4
  { x: 130, y: 245 },
  { x: 200, y: 240 },
  { x: 270, y: 245 },
  // L5 — narrowing
  { x: 155, y: 300 },
  { x: 200, y: 295 },
  { x: 245, y: 300 },
  // L6 — brainstem
  { x: 200, y: 355 },
]

const EDGES: Array<[number, number]> = [
  [0, 2], [0, 3], [1, 3], [1, 4], [0, 1],
  [2, 5], [2, 6], [3, 6], [3, 7], [4, 7], [4, 8],
  [5, 9], [6, 9], [6, 10], [7, 10], [7, 11], [8, 11],
  [6, 7],
  [9, 12], [10, 12], [10, 13], [10, 14], [11, 13], [11, 14],
  [12, 15], [13, 15], [14, 15],
  [12, 13], [13, 14],
]

// Deterministic pseudo-random in [0, 1) — seeded by index so timings are
// stable across re-renders and don't jitter.
const rng = (i: number, factor: number) => {
  const v = Math.sin((i + 1) * factor) * 43758.5453
  return v - Math.floor(v)
}

function edgePath(idx: number): string {
  const [fromIdx, toIdx] = EDGES[idx]
  const a = NODES[fromIdx]
  const b = NODES[toIdx]
  const mx = (a.x + b.x) / 2
  const my = (a.y + b.y) / 2
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  // Push the control point perpendicular to the segment for an organic curve.
  const offsetMag = 6 + rng(idx, 1.7) * 16
  const sign = idx % 2 === 0 ? 1 : -1
  const cx = mx - (dy / len) * offsetMag * sign
  const cy = my + (dx / len) * offsetMag * sign
  return `M ${a.x.toFixed(1)},${a.y.toFixed(1)} Q ${cx.toFixed(1)},${cy.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`
}

interface BrainLoadingAnimationProps {
  done?: boolean
}

export default function BrainLoadingAnimation({ done = false }: BrainLoadingAnimationProps) {
  return (
    <div
      className="min-h-screen w-full flex flex-col items-center justify-center px-8 py-12"
      style={{ background: COLORS.bg }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
      >
        <svg
          viewBox="0 0 400 400"
          width="420"
          height="420"
          className="max-w-full overflow-visible"
          aria-hidden="true"
        >
          <defs>
            <filter id="brainGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2.5" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="ringGlow" x="-100%" y="-100%" width="300%" height="300%">
              <feGaussianBlur stdDeviation="5" />
            </filter>
            <radialGradient id="nodeCore" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={COLORS.tertiary} stopOpacity="1" />
              <stop offset="60%" stopColor={COLORS.secondary} stopOpacity="1" />
              <stop offset="100%" stopColor={COLORS.primary} stopOpacity="1" />
            </radialGradient>
          </defs>

          {/* Connections + traveling particles */}
          <g>
            {EDGES.map((_, i) => {
              const path = edgePath(i)
              const dur = (1.8 + rng(i, 0.9) * 2.2).toFixed(2)
              const begin = (rng(i, 1.3) * 2.5).toFixed(2)
              const pDur = (2.8 + rng(i, 2.1) * 3.2).toFixed(2)
              const pBegin = (rng(i, 1.7) * 5).toFixed(2)
              return (
                <g key={i}>
                  <path
                    d={path}
                    fill="none"
                    stroke={COLORS.primary}
                    strokeWidth="0.9"
                    strokeOpacity="0.18"
                    strokeLinecap="round"
                    filter="url(#brainGlow)"
                  >
                    <animate
                      attributeName="stroke-opacity"
                      values="0.1;0.55;0.1"
                      dur={`${dur}s`}
                      begin={`${begin}s`}
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="stroke-width"
                      values="0.7;1.6;0.7"
                      dur={`${dur}s`}
                      begin={`${begin}s`}
                      repeatCount="indefinite"
                    />
                  </path>
                  <circle r="1.8" fill={COLORS.tertiary} filter="url(#brainGlow)">
                    <animateMotion
                      dur={`${pDur}s`}
                      begin={`${pBegin}s`}
                      repeatCount="indefinite"
                      path={path}
                    />
                    <animate
                      attributeName="opacity"
                      values="0;1;1;0"
                      keyTimes="0;0.15;0.85;1"
                      dur={`${pDur}s`}
                      begin={`${pBegin}s`}
                      repeatCount="indefinite"
                    />
                  </circle>
                </g>
              )
            })}
          </g>

          {/* Nodes */}
          <g>
            {NODES.map((n, i) => {
              const ringDur = (2 + rng(i, 0.8) * 1.8).toFixed(2)
              const ringBegin = (rng(i, 1.5) * 2).toFixed(2)
              const midDur = (1.6 + rng(i, 1.1) * 1.4).toFixed(2)
              return (
                <g key={i}>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r="9"
                    fill={COLORS.primary}
                    opacity="0.18"
                    filter="url(#ringGlow)"
                  >
                    <animate
                      attributeName="r"
                      values="7;15;7"
                      dur={`${ringDur}s`}
                      begin={`${ringBegin}s`}
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="opacity"
                      values="0.05;0.4;0.05"
                      dur={`${ringDur}s`}
                      begin={`${ringBegin}s`}
                      repeatCount="indefinite"
                    />
                  </circle>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r="4.5"
                    fill={COLORS.secondary}
                    opacity="0.55"
                    filter="url(#brainGlow)"
                  >
                    <animate
                      attributeName="opacity"
                      values="0.35;0.85;0.35"
                      dur={`${midDur}s`}
                      begin={`${ringBegin}s`}
                      repeatCount="indefinite"
                    />
                  </circle>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r="2.4"
                    fill="url(#nodeCore)"
                    filter="url(#brainGlow)"
                  />
                </g>
              )
            })}
          </g>
        </svg>
      </motion.div>

      <motion.div
        className="text-center mt-6"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.3, ease: "easeOut" }}
      >
        <h2
          className="text-2xl font-light tracking-wide"
          style={{ color: "#e8eaf0" }}
        >
          {done ? "Analysis Complete" : "Analyzing Brain Response"}
        </h2>
        <p
          className="text-sm mt-2 max-w-sm mx-auto"
          style={{ color: "rgba(196,181,253,0.55)" }}
        >
          {done
            ? "Finalizing visualization…"
            : "Processing neural patterns and cognitive signals…"}
        </p>
      </motion.div>

      <motion.div
        className="flex gap-2 mt-6"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.5 }}
      >
        {[0, 1, 2].map(i => (
          <motion.div
            key={i}
            className="w-2 h-2 rounded-full"
            style={{
              background: COLORS.secondary,
              boxShadow: `0 0 10px ${COLORS.primary}, 0 0 4px ${COLORS.primary}`,
            }}
            animate={{ opacity: [0.25, 1, 0.25], scale: [0.85, 1.15, 0.85] }}
            transition={{
              duration: 1.4,
              repeat: Infinity,
              delay: i * 0.18,
              ease: "easeInOut",
            }}
          />
        ))}
      </motion.div>
    </div>
  )
}
