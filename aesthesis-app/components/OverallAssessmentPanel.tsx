"use client"

import { motion } from "framer-motion"
import type { OverallAssessment } from "@/lib/types"

interface OverallAssessmentPanelProps {
  assessment: OverallAssessment
}

const ACCENT = "#7C9CFF"

export default function OverallAssessmentPanel({ assessment }: OverallAssessmentPanelProps) {
  return (
    <motion.div
      className="panel rounded-2xl p-6 relative overflow-hidden"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2 }}
      style={{ border: "1px solid rgba(124,156,255,0.25)" }}
    >
      {/* Background glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: `radial-gradient(ellipse at 50% 0%, rgba(124,156,255,0.12) 0%, transparent 65%)` }}
      />

      <div className="relative z-10 flex flex-col gap-5">
        {/* Header */}
        <div className="flex items-center gap-3">
          <motion.div
            className="w-10 h-10 rounded-full flex items-center justify-center"
            style={{ background: `${ACCENT}18`, color: ACCENT, border: `2px solid ${ACCENT}40` }}
            initial={{ scale: 0, rotate: -30 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ delay: 0.3, type: "spring", stiffness: 300 }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
          </motion.div>

          <div>
            <p className="text-xs tracking-widest uppercase mb-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>
              Neural Assessment
            </p>
            <motion.h3
              className="text-xl font-semibold"
              style={{ color: ACCENT }}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 }}
            >
              Overall reaction
            </motion.h3>
          </div>
        </div>

        {/* Divider */}
        <div style={{ height: "1px", background: `linear-gradient(90deg, ${ACCENT}30, transparent)` }} />

        {/* Summary */}
        <motion.p
          className="text-sm leading-relaxed"
          style={{ color: "rgba(255,255,255,0.78)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45 }}
        >
          {assessment.summary_paragraph}
        </motion.p>

        {/* Strengths + concerns side by side */}
        <div className="grid grid-cols-2 gap-4">
          <Bullets
            title="Strengths"
            items={assessment.top_strengths}
            color="#5CF2C5"
          />
          <Bullets
            title="Concerns"
            items={assessment.top_concerns}
            color="#FF6B6B"
          />
        </div>

        {/* Decisive moment */}
        <div
          className="flex items-start gap-2 p-3 rounded-lg"
          style={{
            background: `${ACCENT}08`,
            border: `1px solid ${ACCENT}20`,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth="1.7" className="shrink-0 mt-0.5">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <div>
            <p className="text-[10px] tracking-widest uppercase mb-1" style={{ color: ACCENT, opacity: 0.7 }}>
              Decisive moment
            </p>
            <p className="text-xs leading-relaxed" style={{ color: "rgba(255,255,255,0.78)" }}>
              {assessment.decisive_moment}
            </p>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

function Bullets({ title, items, color }: { title: string; items: string[]; color: string }) {
  if (!items.length) return null
  return (
    <div className="flex flex-col gap-2">
      <p className="text-[10px] tracking-widest uppercase" style={{ color: `${color}cc` }}>
        {title}
      </p>
      <ul className="flex flex-col gap-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2 text-xs leading-relaxed"
              style={{ color: "rgba(255,255,255,0.68)" }}>
            <span
              className="w-1 h-1 rounded-full shrink-0 mt-1.5"
              style={{ background: color }}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
