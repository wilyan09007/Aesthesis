"use client"

import { useEffect, useRef, useState } from "react"
import { motion } from "framer-motion"
import BrainLoadingAnimation from "./BrainLoadingAnimation"

interface AnalyzingViewProps {
  onComplete: () => void
  // When `error` is non-null the request failed. When `isResolved` is true
  // the response is in and we can transition to the "complete" state.
  error?: string | null
  isResolved?: boolean
  onRetry?: () => void
  onCancel?: () => void
}

export default function AnalyzingView({
  onComplete,
  error,
  isResolved = false,
  onRetry,
  onCancel,
}: AnalyzingViewProps) {
  const [done, setDone] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  // Once the backend resolves, briefly hold the "complete" state so the
  // loading screen has time to acknowledge it before unmounting.
  useEffect(() => {
    if (!isResolved || done) return
    const t = setTimeout(() => setDone(true), 500)
    return () => clearTimeout(t)
  }, [isResolved, done])

  useEffect(() => {
    if (done) {
      const t = setTimeout(() => onCompleteRef.current(), 600)
      return () => clearTimeout(t)
    }
  }, [done])

  if (error) {
    return (
      <div
        className="min-h-screen flex flex-col items-center justify-center px-8"
        style={{ background: "#0a0a0f" }}
      >
        <motion.div
          className="text-center mb-12"
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div
            className="inline-flex items-center gap-2 mb-4 px-3 py-1 rounded-full text-xs tracking-widest uppercase"
            style={{
              background: "rgba(255,107,107,0.08)",
              border: "1px solid rgba(255,107,107,0.25)",
              color: "#FF6B6B",
            }}
          >
            <div className="w-1.5 h-1.5 rounded-full" style={{ background: "#FF6B6B" }} />
            Failed
          </div>
          <h2 className="text-2xl font-light" style={{ color: "#e8eaf0" }}>
            Analysis failed
          </h2>
          <p className="text-sm mt-2" style={{ color: "rgba(255,255,255,0.4)" }}>
            The backend rejected this run. Details below.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-2xl mb-8 p-5 rounded-2xl"
          style={{
            background: "rgba(255,107,107,0.06)",
            border: "1px solid rgba(255,107,107,0.25)",
          }}
        >
          <p className="text-xs uppercase tracking-widest mb-2" style={{ color: "#FF6B6B" }}>
            Backend error
          </p>
          <p className="text-sm leading-relaxed font-mono" style={{ color: "rgba(255,255,255,0.85)" }}>
            {error}
          </p>
          <div className="mt-4 flex gap-3">
            {onRetry && (
              <button
                onClick={onRetry}
                className="px-4 py-2 rounded-lg text-xs font-medium transition-all"
                style={{
                  background: "rgba(139,92,246,0.15)",
                  border: "1px solid rgba(139,92,246,0.3)",
                  color: "#a78bfa",
                  cursor: "pointer",
                }}
              >
                Retry
              </button>
            )}
            {onCancel && (
              <button
                onClick={onCancel}
                className="px-4 py-2 rounded-lg text-xs font-medium transition-all"
                style={{
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  color: "rgba(255,255,255,0.6)",
                  cursor: "pointer",
                }}
              >
                Start over
              </button>
            )}
          </div>
        </motion.div>
      </div>
    )
  }

  return <BrainLoadingAnimation done={done} />
}
