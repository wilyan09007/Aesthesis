"use client"

import { motion } from "framer-motion"

interface VerdictPanelProps {
  winner: "A" | "B" | "tie"
  summary: string
}

const winnerConfig = {
  A: { color: "#7C9CFF", label: "Version A Wins", glow: "rgba(124,156,255,0.15)", border: "rgba(124,156,255,0.25)" },
  B: { color: "#5CF2C5", label: "Version B Wins", glow: "rgba(92,242,197,0.12)", border: "rgba(92,242,197,0.22)" },
  tie: { color: "#FBBF24", label: "Tie", glow: "rgba(251,191,36,0.1)", border: "rgba(251,191,36,0.2)" },
}

export default function VerdictPanel({ winner, summary }: VerdictPanelProps) {
  const cfg = winnerConfig[winner]

  return (
    <motion.div
      className="panel rounded-2xl p-6 relative overflow-hidden"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2 }}
      style={{ border: `1px solid ${cfg.border}` }}
    >
      {/* Background glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: `radial-gradient(ellipse at 50% 0%, ${cfg.glow} 0%, transparent 65%)` }}
      />

      <div className="relative z-10">
        {/* Header */}
        <div className="flex items-center gap-3 mb-5">
          <motion.div
            className="w-10 h-10 rounded-full flex items-center justify-center text-lg font-bold"
            style={{ background: `${cfg.color}18`, color: cfg.color, border: `2px solid ${cfg.color}40` }}
            initial={{ scale: 0, rotate: -30 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ delay: 0.3, type: "spring", stiffness: 300 }}
          >
            {winner === "tie" ? "=" : winner}
          </motion.div>

          <div>
            <p className="text-xs tracking-widest uppercase mb-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>
              Neural Verdict
            </p>
            <motion.h3
              className="text-xl font-semibold"
              style={{ color: cfg.color }}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 }}
            >
              {cfg.label}
            </motion.h3>
          </div>

          {/* Trophy icon */}
          <motion.div
            className="ml-auto"
            style={{ color: cfg.color, opacity: 0.6 }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.6 }}
            transition={{ delay: 0.5 }}
          >
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M6 9H4.5a2.5 2.5 0 010-5H6" />
              <path d="M18 9h1.5a2.5 2.5 0 000-5H18" />
              <path d="M4 22h16" />
              <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
              <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
              <path d="M18 2H6v7a6 6 0 0012 0V2z" />
            </svg>
          </motion.div>
        </div>

        {/* Divider */}
        <div className="mb-4" style={{ height: "1px", background: `linear-gradient(90deg, ${cfg.color}30, transparent)` }} />

        {/* Summary */}
        <motion.p
          className="text-sm leading-relaxed"
          style={{ color: "rgba(255,255,255,0.65)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45 }}
        >
          {summary}
        </motion.p>
      </div>
    </motion.div>
  )
}
