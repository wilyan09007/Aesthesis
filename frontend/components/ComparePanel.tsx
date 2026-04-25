"use client"

import { useEffect, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { CompareResult } from "@/lib/db/runs"
import { ROI_KEYS, ROI_LABELS, ROI_COLORS } from "@/lib/types"

interface ComparePanelProps {
  currentRunId: string
  pastRunId: string
  onClose: () => void
}

function WinnerBadge({ winner }: { winner: string }) {
  const cfg =
    winner === "A"
      ? { color: "#7C9CFF", bg: "rgba(124,156,255,0.15)", label: "Version A" }
      : winner === "B"
        ? { color: "#5CF2C5", bg: "rgba(92,242,197,0.12)", label: "Version B" }
        : { color: "#FBBF24", bg: "rgba(251,191,36,0.1)", label: "Tie" }

  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
      style={{ background: cfg.bg, color: cfg.color }}>
      {cfg.label}
    </span>
  )
}

function DeltaBar({ value, color }: { value: number; color: string }) {
  const pct = Math.min(Math.abs(value) * 100 * 3, 100)
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.06)" }}>
        <div className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: value >= 0 ? color : "#FF6B6B", opacity: 0.8 }} />
      </div>
      <span className="text-[10px] font-mono w-12 text-right"
        style={{ color: value >= 0 ? color : "#FF6B6B" }}>
        {value >= 0 ? "+" : ""}{(value * 100).toFixed(1)}%
      </span>
    </div>
  )
}

export default function ComparePanel({ currentRunId, pastRunId, onClose }: ComparePanelProps) {
  const [result, setResult] = useState<CompareResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(false)
    fetch(`/api/runs/${currentRunId}/compare?with=${pastRunId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setResult)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [currentRunId, pastRunId])

  const topDeltas = result
    ? ROI_KEYS
        .map((k) => ({
          key: k,
          a: result.delta[k].a,
          b: result.delta[k].b,
          abs: Math.abs(result.delta[k].a) + Math.abs(result.delta[k].b),
        }))
        .sort((x, y) => y.abs - x.abs)
        .slice(0, 5)
    : []

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex items-center justify-center p-8"
        style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(8px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      >
        <motion.div
          className="relative w-full max-w-2xl rounded-2xl overflow-hidden"
          style={{ background: "rgba(11,15,20,0.99)", border: "1px solid rgba(255,255,255,0.1)", maxHeight: "85vh", overflowY: "auto" }}
          initial={{ opacity: 0, scale: 0.95, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95 }}
          transition={{ duration: 0.2 }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-5"
            style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
            <div>
              <h2 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Run Comparison</h2>
              <p className="text-xs mt-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                Current vs past · avg ROI delta across all frames
              </p>
            </div>
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "rgba(255,255,255,0.35)" }}
              onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
              onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.35)")}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Body */}
          <div className="p-6">
            {loading && (
              <div className="flex items-center justify-center h-40">
                <motion.div className="w-8 h-8 rounded-full"
                  style={{ border: "2px solid rgba(124,156,255,0.3)", borderTopColor: "#7C9CFF" }}
                  animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }} />
              </div>
            )}

            {error && !loading && (
              <p className="text-sm text-center py-10" style={{ color: "rgba(255,255,255,0.35)" }}>
                Could not load comparison data.
              </p>
            )}

            {result && !loading && (
              <div className="flex flex-col gap-6">
                {/* Run summaries */}
                <div className="grid grid-cols-2 gap-4">
                  {(["current", "past"] as const).map((which) => {
                    const r = result[which]
                    return (
                      <div key={which} className="rounded-xl p-4"
                        style={{
                          background: which === "current" ? "rgba(124,156,255,0.05)" : "rgba(255,255,255,0.02)",
                          border: `1px solid ${which === "current" ? "rgba(124,156,255,0.15)" : "rgba(255,255,255,0.06)"}`,
                        }}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[10px] uppercase tracking-widest"
                            style={{ color: "rgba(255,255,255,0.3)" }}>
                            {which}
                          </span>
                          <WinnerBadge winner={r.winner} />
                        </div>
                        {r.goal && (
                          <p className="text-xs mb-2 truncate" style={{ color: "rgba(255,255,255,0.45)" }}>
                            {r.goal}
                          </p>
                        )}
                        <p className="text-xs leading-relaxed line-clamp-3" style={{ color: "rgba(255,255,255,0.55)" }}>
                          {r.summaryText}
                        </p>
                      </div>
                    )
                  })}
                </div>

                {/* ROI deltas — top 5 movers */}
                <div>
                  <p className="text-xs mb-3" style={{ color: "rgba(255,255,255,0.3)" }}>
                    Top ROI shifts (current − past)
                  </p>
                  <div className="flex flex-col gap-3">
                    {topDeltas.map(({ key, a, b }) => (
                      <div key={key}>
                        <div className="flex items-center justify-between mb-1.5">
                          <div className="flex items-center gap-1.5">
                            <div className="w-2 h-2 rounded-full" style={{ background: ROI_COLORS[key] }} />
                            <span className="text-xs" style={{ color: "rgba(255,255,255,0.5)" }}>
                              {ROI_LABELS[key]}
                            </span>
                          </div>
                          <span className="text-[10px]" style={{ color: "rgba(255,255,255,0.25)" }}>A / B</span>
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] w-3" style={{ color: "#7C9CFF" }}>A</span>
                            <DeltaBar value={a} color={ROI_COLORS[key]} />
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] w-3" style={{ color: "#5CF2C5" }}>B</span>
                            <DeltaBar value={b} color={ROI_COLORS[key]} />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
