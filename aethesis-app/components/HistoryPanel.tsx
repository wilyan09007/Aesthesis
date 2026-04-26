"use client"

import { useEffect, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { RunListItem } from "@/lib/db/runs"

interface HistoryPanelProps {
  open: boolean
  onClose: () => void
  savedRunId: string | null
  onSaveFirst: () => Promise<string>
  onCompare: (pastRunId: string) => void
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

const WINNER_STYLE = {
  A: { color: "#7C9CFF", bg: "rgba(124,156,255,0.12)" },
  B: { color: "#5CF2C5", bg: "rgba(92,242,197,0.10)" },
  tie: { color: "#FBBF24", bg: "rgba(251,191,36,0.10)" },
}

export default function HistoryPanel({ open, onClose, savedRunId, onSaveFirst, onCompare }: HistoryPanelProps) {
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [comparingId, setComparingId] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetch("/api/runs")
      .then((r) => r.json())
      .then(({ runs: data }) => setRuns(data ?? []))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false))
  }, [open])

  const handleCompare = async (pastRunId: string) => {
    setComparingId(pastRunId)
    try {
      let currentId = savedRunId
      if (!currentId) currentId = await onSaveFirst()
      onCompare(pastRunId)
      onClose()
    } finally {
      setComparingId(null)
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40"
            style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(4px)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          <motion.div
            className="fixed right-0 top-0 bottom-0 z-50 flex flex-col"
            style={{
              width: 400,
              background: "rgba(11,15,20,0.98)",
              borderLeft: "1px solid rgba(255,255,255,0.08)",
              backdropFilter: "blur(24px)",
            }}
            initial={{ x: 400 }}
            animate={{ x: 0 }}
            exit={{ x: 400 }}
            transition={{ type: "spring", damping: 30, stiffness: 280 }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-5"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <div>
                <h2 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Analysis History</h2>
                <p className="text-xs mt-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                  Saved runs · click to compare
                </p>
              </div>
              <button onClick={onClose} className="p-1.5 rounded-lg transition-colors"
                style={{ color: "rgba(255,255,255,0.35)" }}
                onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
                onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.35)")}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-4 py-4">
              {loading && (
                <div className="flex flex-col gap-3">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="rounded-xl p-4 animate-pulse"
                      style={{ background: "rgba(255,255,255,0.03)", height: 88 }} />
                  ))}
                </div>
              )}

              {!loading && runs.length === 0 && (
                <div className="flex flex-col items-center justify-center h-48 text-center">
                  <p className="text-sm" style={{ color: "rgba(255,255,255,0.3)" }}>No saved runs yet</p>
                  <p className="text-xs mt-1" style={{ color: "rgba(255,255,255,0.18)" }}>
                    Complete an analysis and click Save
                  </p>
                </div>
              )}

              {!loading && runs.length > 0 && (
                <div className="flex flex-col gap-3">
                  {runs.map((run) => {
                    const winner = run.summary?.winner as "A" | "B" | "tie" | undefined
                    const style = winner ? WINNER_STYLE[winner] : null
                    const isCurrent = run.id === savedRunId
                    const isComparing = comparingId === run.id

                    return (
                      <div key={run.id}
                        className="rounded-xl p-4"
                        style={{
                          background: isCurrent ? "rgba(124,156,255,0.06)" : "rgba(255,255,255,0.03)",
                          border: `1px solid ${isCurrent ? "rgba(124,156,255,0.2)" : "rgba(255,255,255,0.06)"}`,
                        }}>
                        <div className="flex items-start justify-between gap-3 mb-2">
                          <div className="flex items-center gap-2 flex-1 min-w-0">
                            {style && winner && (
                              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0"
                                style={{ background: style.bg, color: style.color }}>
                                {winner === "tie" ? "TIE" : `V${winner}`}
                              </span>
                            )}
                            <p className="text-xs truncate" style={{ color: "rgba(255,255,255,0.5)" }}>
                              {run.goal ?? run.urlA ?? "Untitled run"}
                            </p>
                          </div>
                          {isCurrent && (
                            <span className="text-[10px] shrink-0" style={{ color: "#7C9CFF" }}>current</span>
                          )}
                        </div>

                        {run.summary?.summaryText && (
                          <p className="text-xs leading-relaxed mb-3 line-clamp-2"
                            style={{ color: "rgba(255,255,255,0.38)" }}>
                            {run.summary.summaryText}
                          </p>
                        )}

                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-mono"
                            style={{ color: "rgba(255,255,255,0.22)" }}>
                            {relativeTime(run.createdAt as unknown as string)}
                          </span>

                          {!isCurrent && run.summary && (
                            <motion.button
                              onClick={() => handleCompare(run.id)}
                              disabled={isComparing}
                              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium"
                              style={{
                                background: "rgba(124,156,255,0.1)",
                                border: "1px solid rgba(124,156,255,0.2)",
                                color: isComparing ? "rgba(255,255,255,0.3)" : "#7C9CFF",
                              }}
                              whileHover={!isComparing ? { scale: 1.03 } : {}}
                              whileTap={!isComparing ? { scale: 0.97 } : {}}
                            >
                              {isComparing ? (
                                <>
                                  <motion.div className="w-3 h-3 rounded-full border border-current border-t-transparent"
                                    animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }} />
                                  Saving…
                                </>
                              ) : (
                                <>
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M8 3H5a2 2 0 00-2 2v3M21 8V5a2 2 0 00-2-2h-3M3 16v3a2 2 0 002 2h3M16 21h3a2 2 0 002-2v-3" />
                                  </svg>
                                  Compare
                                </>
                              )}
                            </motion.button>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
