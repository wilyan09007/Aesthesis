"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import LiveStreamPanel from "./LiveStreamPanel"
import type { CaptureInputs } from "@/lib/types"

interface CaptureViewProps {
  onContinue: (inputs: CaptureInputs) => void
  onBack: () => void
}

export default function CaptureView({ onContinue, onBack }: CaptureViewProps) {
  const [urlA, setUrlA] = useState("")
  const [urlB, setUrlB] = useState("")
  const [goal, setGoal] = useState("")
  const [runId, setRunId] = useState<string | null>(null)
  const [isCapturing, setIsCapturing] = useState(false)

  const canStart = urlA.trim() && urlB.trim()
  const canContinue = runId !== null

  const handleStart = () => {
    setRunId(`run_${Date.now()}`)
    setIsCapturing(true)
  }

  const handleContinue = () => {
    onContinue({ urlA, urlB, goal })
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-8 py-5" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <button onClick={onBack} className="flex items-center gap-2 text-sm transition-colors"
          style={{ color: "rgba(255,255,255,0.4)" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
          onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.4)")}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          Back
        </button>

        <StepIndicator step={1} />

        <div className="w-16" />
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col max-w-5xl mx-auto w-full px-8 py-8 gap-8">
        {/* Inputs */}
        <motion.div
          className="panel rounded-2xl p-6"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h2 className="text-lg font-medium mb-5" style={{ color: "#e8eaf0" }}>Configure Capture</h2>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <InputField label="URL A" placeholder="https://version-a.example.com" value={urlA} onChange={setUrlA} accent="#7C9CFF" />
            <InputField label="URL B" placeholder="https://version-b.example.com" value={urlB} onChange={setUrlB} accent="#5CF2C5" />
          </div>
          <InputField label="Goal (optional)" placeholder="e.g. complete the signup flow" value={goal} onChange={setGoal} accent="#7C9CFF" />

          <div className="flex items-center gap-3 mt-5">
            <motion.button
              onClick={handleStart}
              disabled={!canStart || isCapturing}
              className="px-5 py-2.5 rounded-xl text-sm font-medium transition-opacity"
              style={{
                background: canStart && !isCapturing ? "rgba(124,156,255,0.15)" : "rgba(255,255,255,0.04)",
                border: "1px solid rgba(124,156,255,0.25)",
                color: canStart && !isCapturing ? "#7C9CFF" : "rgba(255,255,255,0.3)",
                cursor: canStart && !isCapturing ? "pointer" : "not-allowed",
              }}
              whileHover={canStart && !isCapturing ? { scale: 1.02 } : {}}
            >
              {isCapturing ? "Capturing…" : "Start Capture"}
            </motion.button>

            {isCapturing && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full" style={{ background: "#FF6B6B", animation: "pulse-glow 1s infinite" }} />
                <span className="text-xs" style={{ color: "rgba(255,255,255,0.4)" }}>Recording live sessions…</span>
              </motion.div>
            )}
          </div>
        </motion.div>

        {/* Live panels */}
        {runId && (
          <motion.div
            className="flex gap-4 flex-1"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
          >
            <LiveStreamPanel runId={runId} version="A" />
            <LiveStreamPanel runId={runId} version="B" />
          </motion.div>
        )}

        {!runId && (
          <motion.div
            className="flex gap-4 flex-1"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
          >
            <EmptyPanel version="A" />
            <EmptyPanel version="B" />
          </motion.div>
        )}

        {/* Continue */}
        <div className="flex justify-end">
          <motion.button
            onClick={handleContinue}
            disabled={!canContinue && !urlA}
            className="flex items-center gap-2 px-6 py-3 rounded-xl text-sm font-medium"
            style={{
              background: "rgba(92,242,197,0.1)",
              border: "1px solid rgba(92,242,197,0.25)",
              color: "#5CF2C5",
              cursor: "pointer",
            }}
            whileHover={{ scale: 1.02, boxShadow: "0 0 20px rgba(92,242,197,0.15)" }}
            whileTap={{ scale: 0.98 }}
          >
            Continue to Assess
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </motion.button>
        </div>
      </div>
    </div>
  )
}

function InputField({ label, placeholder, value, onChange, accent }: {
  label: string
  placeholder: string
  value: string
  onChange: (v: string) => void
  accent: string
}) {
  return (
    <div>
      <label className="block text-xs mb-1.5 tracking-wide" style={{ color: "rgba(255,255,255,0.4)" }}>{label}</label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-all"
        style={{
          background: "rgba(255,255,255,0.04)",
          border: `1px solid rgba(255,255,255,0.08)`,
          color: "#e8eaf0",
        }}
        onFocus={e => (e.currentTarget.style.borderColor = `${accent}40`)}
        onBlur={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)")}
      />
    </div>
  )
}

function EmptyPanel({ version }: { version: "A" | "B" }) {
  const color = version === "A" ? "#7C9CFF" : "#5CF2C5"
  return (
    <div className="flex-1 rounded-xl panel flex items-center justify-center aspect-video">
      <div className="text-center">
        <div className="text-3xl font-light mb-2" style={{ color: `${color}30` }}>{version}</div>
        <p className="text-xs" style={{ color: "rgba(255,255,255,0.2)" }}>Stream will appear here</p>
      </div>
    </div>
  )
}

function StepIndicator({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2 text-xs" style={{ color: "rgba(255,255,255,0.35)" }}>
      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-medium`}
        style={{ background: step >= 1 ? "rgba(124,156,255,0.2)" : "rgba(255,255,255,0.06)", color: step >= 1 ? "#7C9CFF" : "rgba(255,255,255,0.3)" }}>
        1
      </div>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>—</span>
      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-medium`}
        style={{ background: step >= 2 ? "rgba(124,156,255,0.2)" : "rgba(255,255,255,0.06)", color: step >= 2 ? "#7C9CFF" : "rgba(255,255,255,0.3)" }}>
        2
      </div>
    </div>
  )
}