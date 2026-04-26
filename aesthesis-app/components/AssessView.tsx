"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import UploadZone from "./UploadZone"

interface AssessViewProps {
  onAnalyze: (file: File) => void
  onBack: () => void
}

export default function AssessView({ onAnalyze, onBack }: AssessViewProps) {
  const [file, setFile] = useState<File | null>(null)

  const canAnalyze = file !== null

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

        <div className="w-16" />
      </div>

      {/* Content */}
      <div className="flex-1 max-w-2xl mx-auto w-full px-8 py-10 flex flex-col gap-8">
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
          <h2 className="text-2xl font-light mb-2" style={{ color: "#e8eaf0" }}>Upload Your Demo</h2>
          <p className="text-sm" style={{ color: "rgba(255,255,255,0.4)" }}>
            Drop a screen recording of any product flow. The neural pipeline reads it second-by-second and tells you exactly where attention, friction, and intent showed up.
          </p>
        </motion.div>

        {/* Upload zone */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
        >
          <UploadZone file={file} onFile={setFile} />
        </motion.div>

        {/* Requirements note */}
        <motion.div
          className="flex items-start gap-3 p-4 rounded-xl"
          style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.3)" strokeWidth="1.5" className="mt-0.5 shrink-0">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4M12 16h.01" />
          </svg>
          <p className="text-xs leading-relaxed" style={{ color: "rgba(255,255,255,0.3)" }}>
            MP4 recommended. At least 5 seconds. Longer recordings produce richer per-second insights — TRIBE samples a brain frame every 1.5 seconds of footage.
          </p>
        </motion.div>

        {/* Analyze button */}
        <motion.div
          className="flex justify-end"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.25 }}
        >
          <motion.button
            onClick={() => file && onAnalyze(file)}
            disabled={!canAnalyze}
            className="flex items-center gap-2.5 px-8 py-3.5 rounded-xl text-sm font-medium transition-all"
            style={{
              background: canAnalyze ? "rgba(224,69,77,0.2)" : "rgba(255,255,255,0.04)",
              border: canAnalyze ? "1px solid rgba(224,69,77,0.3)" : "1px solid rgba(255,255,255,0.06)",
              color: canAnalyze ? "#e8eaf0" : "rgba(255,255,255,0.25)",
              cursor: canAnalyze ? "pointer" : "not-allowed",
            }}
            whileHover={canAnalyze ? {
              scale: 1.02,
              boxShadow: "0 0 30px rgba(224,69,77,0.2)",
            } : {}}
            whileTap={canAnalyze ? { scale: 0.98 } : {}}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
            Analyze Neural Response
          </motion.button>
        </motion.div>
      </div>
    </div>
  )
}
