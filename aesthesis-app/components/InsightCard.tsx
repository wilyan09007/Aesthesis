"use client"

import { useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { Insight } from "@/lib/types"

interface InsightCardProps {
  insight: Insight
  index: number
  onSeek: (t: number) => void
  runId?: string | null
  goal?: string | null
}

const ACCENT = "#7C9CFF"
const VIOLET = "#a78bfa"

export default function InsightCard({ insight, index, onSeek, runId, goal }: InsightCardProps) {
  const [t0, t1] = insight.timestamp_range_s
  const [expanded, setExpanded] = useState(false)
  const [suggestion, setSuggestion] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchSuggestion = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (loading || suggestion) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch("/api/agent/insight-fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          insight,
          currentRunId: runId ?? null,
          goal: goal ?? null,
          threadId: crypto.randomUUID(),
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error ?? `Request failed: ${res.status}`)
      setSuggestion(data.suggestion ?? "")
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const toggle = () => setExpanded(v => !v)
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      toggle()
    }
  }

  return (
    <motion.div
      className="w-full p-4 rounded-xl cursor-pointer transition-all"
      style={{
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${expanded ? `${ACCENT}30` : "rgba(255,255,255,0.06)"}`,
      }}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.07 }}
      whileHover={{ backgroundColor: "rgba(255,255,255,0.05)" }}
      onClick={toggle}
      onKeyDown={onKey}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
    >
      {/* Header — timestamp + seek + chevron */}
      <div className="flex items-center gap-2 mb-2">
        <span
          className="flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono"
          style={{ background: `${ACCENT}12`, color: ACCENT, border: `1px solid ${ACCENT}25` }}
        >
          {t0.toFixed(1)}s – {t1.toFixed(1)}s
        </span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onSeek(t0) }}
          className="flex items-center justify-center w-5 h-5 rounded transition-opacity hover:opacity-100 opacity-50"
          style={{ color: "rgba(255,255,255,0.6)" }}
          title="Seek video to this moment"
          aria-label="Seek video to this moment"
        >
          <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
        </button>
        <motion.svg
          className="ml-auto"
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="rgba(255,255,255,0.4)" strokeWidth="2"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <path d="M6 9l6 6 6-6" />
        </motion.svg>
      </div>

      {/* Short phrase (full text when expanded) */}
      <p
        className={`text-xs leading-relaxed ${expanded ? "" : "line-clamp-1"}`}
        style={{ color: "rgba(255,255,255,0.85)" }}
      >
        {insight.ux_observation}
      </p>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="expanded"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="pt-3 mt-3 border-t" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
              {/* Initial recommendation from the analysis */}
              <div className="flex items-start gap-2 mb-3">
                <div
                  className="w-3.5 h-3.5 rounded-full flex items-center justify-center shrink-0 mt-0.5"
                  style={{ background: `${ACCENT}15` }}
                >
                  <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth="2.5">
                    <path d="M5 12h14" />
                    <path d="M12 5l7 7-7 7" />
                  </svg>
                </div>
                <div className="flex-1">
                  <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "rgba(255,255,255,0.35)" }}>
                    Initial recommendation
                  </p>
                  <p className="text-xs leading-relaxed" style={{ color: ACCENT, opacity: 0.85 }}>
                    {insight.recommendation}
                  </p>
                </div>
              </div>

              {insight.cited_brain_features.length > 0 && (
                <div className="mb-3">
                  <p className="text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                    Cited brain signals
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {insight.cited_brain_features.map((feat, i) => (
                      <span
                        key={i}
                        className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                        style={{ background: "rgba(255,255,255,0.04)", color: "rgba(255,255,255,0.6)" }}
                      >
                        {feat}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Backboard-powered personalized fix */}
              <div className="mt-3 pt-3 border-t" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
                {!suggestion && !loading && !error && (
                  <button
                    type="button"
                    onClick={fetchSuggestion}
                    className="w-full px-3 py-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all hover:brightness-125"
                    style={{
                      background: `${VIOLET}15`,
                      border: `1px solid ${VIOLET}40`,
                      color: VIOLET,
                    }}
                  >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z" />
                    </svg>
                    Get personalized fix
                  </button>
                )}
                {loading && (
                  <div className="flex items-center justify-center gap-1.5 py-3">
                    {[0, 1, 2].map(i => (
                      <motion.span
                        key={i}
                        className="w-1.5 h-1.5 rounded-full"
                        style={{ background: VIOLET }}
                        animate={{ opacity: [0.3, 1, 0.3] }}
                        transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
                      />
                    ))}
                    <span className="text-[10px] ml-2" style={{ color: "rgba(255,255,255,0.4)" }}>
                      Backboard is generating a personalized fix…
                    </span>
                  </div>
                )}
                {error && (
                  <div className="space-y-2">
                    <div
                      className="text-xs py-2 px-3 rounded"
                      style={{ background: "rgba(255,107,107,0.08)", color: "#FF6B6B", border: "1px solid rgba(255,107,107,0.2)" }}
                    >
                      {error}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setError(null); fetchSuggestion(e) }}
                      className="text-[10px] underline"
                      style={{ color: "rgba(255,255,255,0.5)" }}
                    >
                      Try again
                    </button>
                  </div>
                )}
                {suggestion && (
                  <div>
                    <p className="text-[10px] uppercase tracking-widest mb-2 flex items-center gap-1" style={{ color: VIOLET }}>
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z" />
                      </svg>
                      Personalized fix · Backboard
                    </p>
                    <p className="text-xs leading-relaxed whitespace-pre-wrap" style={{ color: "rgba(255,255,255,0.85)" }}>
                      {suggestion}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
