"use client"

import { motion } from "framer-motion"
import type { Insight } from "@/lib/types"

interface InsightCardProps {
  insight: Insight
  index: number
  version: "A" | "B"
  onSeek: (t: number) => void
}

export default function InsightCard({ insight, index, version, onSeek }: InsightCardProps) {
  const accent = version === "A" ? "#7C9CFF" : "#5CF2C5"
  const [t0, t1] = insight.timestamp_range_s

  return (
    <motion.button
      className="w-full text-left p-4 rounded-xl transition-all group"
      style={{
        background: "rgba(255,255,255,0.03)",
        border: "1px solid rgba(255,255,255,0.06)",
      }}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.07 }}
      onClick={() => onSeek(t0)}
      whileHover={{
        backgroundColor: "rgba(255,255,255,0.05)",
        borderColor: `${accent}30`,
        scale: 1.005,
      }}
      whileTap={{ scale: 0.998 }}
    >
      {/* Timestamp */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono"
            style={{ background: `${accent}12`, color: accent, border: `1px solid ${accent}25` }}>
            {t0.toFixed(1)}s – {t1.toFixed(1)}s
          </div>
        </div>
        <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-xs"
          style={{ color: "rgba(255,255,255,0.3)" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          Seek
        </div>
      </div>

      {/* Observation */}
      <p className="text-xs leading-relaxed mb-2.5" style={{ color: "rgba(255,255,255,0.7)" }}>
        {insight.ux_observation}
      </p>

      {/* Recommendation */}
      <div className="flex items-start gap-2">
        <div className="w-3.5 h-3.5 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: `${accent}15` }}>
          <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke={accent} strokeWidth="2.5">
            <path d="M5 12h14" />
            <path d="M12 5l7 7-7 7" />
          </svg>
        </div>
        <p className="text-xs leading-relaxed" style={{ color: accent, opacity: 0.8 }}>
          {insight.recommendation}
        </p>
      </div>
    </motion.button>
  )
}
