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

const ACCENT = "#E0454D"
const VIOLET = "#a78bfa"
const VIOLET_GLOW = "0 0 22px rgba(139,92,246,0.32), 0 0 48px rgba(139,92,246,0.14)"
const AMBER = "#fbbf24"

// "unclear:" label prefix marks low-confidence picks; the prompt's
// unclear branch already handles them but the card flags them too so
// the user reads the prompt with calibrated trust before pasting.
function isUnclear(insight: Insight): boolean {
  if (insight.confidence < 0.4) return true
  const label = insight.target_element?.label ?? ""
  return label.trim().toLowerCase().startsWith("unclear")
}

function confidenceBand(insight: Insight): "standard" | "cautious" | "unclear" {
  if (isUnclear(insight)) return "unclear"
  if (insight.confidence < 0.7) return "cautious"
  return "standard"
}

export default function InsightCard({ insight, index, onSeek, runId, goal }: InsightCardProps) {
  const [t0, t1] = insight.timestamp_range_s
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)
  const [showLightbox, setShowLightbox] = useState(false)
  // Backboard "compare-to-past" suggestion (demoted from primary action).
  const [bbSuggestion, setBbSuggestion] = useState<string | null>(null)
  const [bbLoading, setBbLoading] = useState(false)
  const [bbError, setBbError] = useState<string | null>(null)

  const band = confidenceBand(insight)
  const target = insight.target_element
  const change = insight.proposed_change
  const annotated = insight.annotated_screenshot_b64

  const cardBorder =
    band === "unclear"
      ? `${AMBER}40`
      : expanded
        ? `${ACCENT}30`
        : "rgba(255,255,255,0.06)"

  const copyPrompt = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!insight.agent_prompt) {
      setCopyError("Agent prompt unavailable for this moment.")
      return
    }
    setCopyError(null)
    try {
      await navigator.clipboard.writeText(insight.agent_prompt)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2200)
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : "Could not copy")
    }
  }

  const downloadAnnotated = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!annotated) return
    const link = document.createElement("a")
    link.href = `data:image/jpeg;base64,${annotated}`
    link.download = `aesthesis-insight-${t0.toFixed(1)}s.jpg`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const fetchBackboardSuggestion = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (bbLoading || bbSuggestion) return
    setBbLoading(true)
    setBbError(null)
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
      setBbSuggestion(data.suggestion ?? "")
    } catch (err) {
      setBbError(err instanceof Error ? err.message : String(err))
    } finally {
      setBbLoading(false)
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
    <>
      <motion.div
        className="w-full p-4 rounded-xl cursor-pointer transition-all"
        style={{
          background: "rgba(255,255,255,0.03)",
          border: `1px solid ${cardBorder}`,
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
          {band !== "standard" && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider"
              style={{
                background: band === "unclear" ? `${AMBER}12` : "rgba(255,255,255,0.04)",
                color: band === "unclear" ? AMBER : "rgba(255,255,255,0.55)",
                border: band === "unclear"
                  ? `1px solid ${AMBER}25`
                  : "1px solid rgba(255,255,255,0.08)",
              }}
              title={
                band === "unclear"
                  ? "Low confidence — this prompt asks the agent to investigate rather than commit a fix"
                  : "Medium confidence — verify the element matches before applying"
              }
            >
              {band === "unclear" ? "low conf." : "verify"}
            </span>
          )}
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
                {/* Target element block */}
                {target && (
                  <div className="mb-3">
                    <p className="text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                      Target element
                    </p>
                    <div className="flex gap-3 items-start">
                      {annotated && (
                        <button
                          type="button"
                          className="shrink-0 rounded-md overflow-hidden"
                          style={{
                            width: 96,
                            height: 64,
                            border: `1px solid ${ACCENT}30`,
                            background: "rgba(0,0,0,0.4)",
                          }}
                          onClick={(e) => { e.stopPropagation(); setShowLightbox(true) }}
                          title="Click to enlarge"
                        >
                          <img
                            src={`data:image/jpeg;base64,${annotated}`}
                            alt={target.label}
                            className="w-full h-full object-cover"
                          />
                        </button>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium leading-tight mb-0.5" style={{ color: "#e8eaf0" }}>
                          {target.label}
                        </p>
                        {target.visible_text && (
                          <p className="text-xs font-mono mb-0.5" style={{ color: "rgba(255,255,255,0.6)" }}>
                            &ldquo;{target.visible_text}&rdquo;
                          </p>
                        )}
                        {target.location_hint && (
                          <p className="text-[11px] mb-1" style={{ color: "rgba(255,255,255,0.5)" }}>
                            {target.location_hint}
                          </p>
                        )}
                        {target.visual_anchors.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {target.visual_anchors.map((a, i) => (
                              <span
                                key={i}
                                className="px-1.5 py-0.5 rounded text-[10px]"
                                style={{ background: "rgba(255,255,255,0.04)", color: "rgba(255,255,255,0.55)" }}
                              >
                                {a}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {/* Proposed change block */}
                {change && (
                  <div className="mb-3 p-2 rounded-md" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <p className="text-[10px] uppercase tracking-widest mb-1.5 flex items-center gap-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                      Change
                      <span
                        className="px-1.5 py-0 rounded text-[10px] font-mono normal-case tracking-normal"
                        style={{ background: `${VIOLET}14`, color: VIOLET, border: `1px solid ${VIOLET}30` }}
                      >
                        {change.change_type}
                      </span>
                    </p>
                    <div className="space-y-1">
                      <p className="text-xs leading-relaxed">
                        <span className="text-[10px] uppercase tracking-wider mr-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>from</span>
                        <span style={{ color: "rgba(255,255,255,0.7)" }}>{change.current_state}</span>
                      </p>
                      <p className="text-xs leading-relaxed">
                        <span className="text-[10px] uppercase tracking-wider mr-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>to</span>
                        <span style={{ color: "#e8eaf0" }}>{change.desired_state}</span>
                      </p>
                      {change.rationale && (
                        <p className="text-[11px] leading-relaxed pt-1" style={{ color: "rgba(255,255,255,0.55)" }}>
                          {change.rationale}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {/* Acceptance criteria */}
                {insight.acceptance_criteria.length > 0 && (
                  <div className="mb-3">
                    <p className="text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "rgba(255,255,255,0.35)" }}>
                      Acceptance criteria
                    </p>
                    <ul className="space-y-1">
                      {insight.acceptance_criteria.map((c, i) => (
                        <li key={i} className="flex items-start gap-2 text-[11px] leading-relaxed">
                          <span
                            className="mt-0.5 shrink-0"
                            style={{
                              width: 10, height: 10,
                              border: `1px solid ${VIOLET}55`,
                              borderRadius: 2,
                            }}
                          />
                          <span style={{ color: "rgba(255,255,255,0.75)" }}>{c}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Cited brain features */}
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

                {/* Primary action: copy agent prompt */}
                <div className="mt-3 pt-3 border-t flex flex-col gap-2" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
                  <button
                    type="button"
                    onClick={copyPrompt}
                    disabled={!insight.agent_prompt}
                    className="w-full px-3 py-2.5 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all hover:brightness-125 disabled:opacity-40 disabled:cursor-not-allowed"
                    style={{
                      background: copied
                        ? `${VIOLET}30`
                        : `${VIOLET}1f`,
                      border: `1px solid ${VIOLET}55`,
                      color: VIOLET,
                      boxShadow: copied ? VIOLET_GLOW : undefined,
                    }}
                  >
                    {copied ? (
                      <>
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                        Copied — paste into your AI agent
                      </>
                    ) : (
                      <>
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <rect x="9" y="9" width="13" height="13" rx="2" />
                          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                        </svg>
                        Copy prompt for AI agent
                      </>
                    )}
                  </button>

                  {copyError && (
                    <p className="text-[10px] text-center" style={{ color: "#FF6B6B" }}>
                      {copyError}
                    </p>
                  )}

                  <div className="flex gap-2">
                    {annotated && (
                      <button
                        type="button"
                        onClick={downloadAnnotated}
                        className="flex-1 px-2 py-1.5 rounded-md text-[11px] flex items-center justify-center gap-1.5 transition-colors"
                        style={{
                          background: "rgba(255,255,255,0.03)",
                          border: "1px solid rgba(255,255,255,0.08)",
                          color: "rgba(255,255,255,0.55)",
                        }}
                      >
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                          <polyline points="7 10 12 15 17 10" />
                          <line x1="12" y1="15" x2="12" y2="3" />
                        </svg>
                        Save screenshot
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={fetchBackboardSuggestion}
                      disabled={bbLoading}
                      className="flex-1 px-2 py-1.5 rounded-md text-[11px] flex items-center justify-center gap-1.5 transition-colors disabled:opacity-50"
                      style={{
                        background: "rgba(255,255,255,0.03)",
                        border: "1px solid rgba(255,255,255,0.08)",
                        color: "rgba(255,255,255,0.55)",
                      }}
                    >
                      {bbLoading ? (
                        <>
                          {[0, 1, 2].map(i => (
                            <motion.span
                              key={i}
                              className="w-1 h-1 rounded-full"
                              style={{ background: "currentColor" }}
                              animate={{ opacity: [0.3, 1, 0.3] }}
                              transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
                            />
                          ))}
                          <span className="ml-1">Comparing…</span>
                        </>
                      ) : (
                        <>
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                            <path d="M3 12a9 9 0 109-9 9.75 9.75 0 00-6.74 2.74L3 8" />
                            <path d="M3 3v5h5" />
                          </svg>
                          Compare to past runs
                        </>
                      )}
                    </button>
                  </div>

                  {bbError && (
                    <p className="text-[10px]" style={{ color: "#FF6B6B" }}>{bbError}</p>
                  )}
                  {bbSuggestion && (
                    <div className="mt-1 p-2 rounded-md" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
                      <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "rgba(255,255,255,0.35)" }}>
                        Backboard · trends across runs
                      </p>
                      <p className="text-[11px] leading-relaxed whitespace-pre-wrap" style={{ color: "rgba(255,255,255,0.75)" }}>
                        {bbSuggestion}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* Lightbox for the annotated screenshot */}
      <AnimatePresence>
        {showLightbox && annotated && (
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-8 cursor-pointer"
            style={{ background: "rgba(0,0,0,0.85)", backdropFilter: "blur(8px)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setShowLightbox(false)}
          >
            <motion.img
              src={`data:image/jpeg;base64,${annotated}`}
              alt={target?.label ?? "annotated screenshot"}
              className="max-w-full max-h-full rounded-lg"
              style={{ border: `2px solid ${ACCENT}` }}
              initial={{ scale: 0.95 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.95 }}
              onClick={(e) => e.stopPropagation()}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
