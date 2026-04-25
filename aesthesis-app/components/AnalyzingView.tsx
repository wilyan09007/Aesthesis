"use client"

import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { VideoFiles } from "@/lib/types"

interface AnalyzingViewProps {
  videoFiles: VideoFiles
  onComplete: () => void
  // Real-network plumbing (page-level state). When `error` is non-null, the
  // request failed. When `isResolved` is true, the response is in and we
  // can let progress run to 100%. While the request is in flight we hold
  // progress at ~95% so the user doesn't see the bar finish before the
  // backend does.
  error?: string | null
  isResolved?: boolean
  onRetry?: () => void
  onCancel?: () => void
}

const STAGES = [
  "Encoding neural features…",
  "Mapping cortical response…",
  "Extracting ROI signals…",
  "Generating insights…",
]

const HOLD_AT_PCT = 95 // hold the last stage at this % until the response arrives

type PanelState = {
  stageIndex: number
  progress: number
  done: boolean
}

function usePanelProgress(
  delay: number,
  onDone: () => void,
  isResolved: boolean,
): PanelState {
  const [stageIndex, setStageIndex] = useState(0)
  const [progress, setProgress] = useState(0)
  const [done, setDone] = useState(false)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone
  const isResolvedRef = useRef(isResolved)
  isResolvedRef.current = isResolved

  useEffect(() => {
    let tick: ReturnType<typeof setInterval> | null = null

    const startTimer = setTimeout(() => {
      let currentStage = 0
      let currentProgress = 0

      tick = setInterval(() => {
        const lastStage = currentStage === STAGES.length - 1

        // Hold at HOLD_AT_PCT during the last stage until the network
        // request resolves. Otherwise the bar would finish in ~3s while
        // the backend takes 12-25s to respond.
        if (lastStage && !isResolvedRef.current && currentProgress >= HOLD_AT_PCT) {
          setProgress(HOLD_AT_PCT)
          return
        }

        currentProgress += Math.random() * 8 + 4

        if (currentProgress >= 100) {
          if (lastStage && !isResolvedRef.current) {
            currentProgress = HOLD_AT_PCT
            setProgress(HOLD_AT_PCT)
            return
          }
          currentProgress = 100
          currentStage++

          if (currentStage >= STAGES.length) {
            if (tick) clearInterval(tick)
            setDone(true)
            onDoneRef.current()
            return
          }

          setStageIndex(currentStage)
          currentProgress = 0
        }

        setProgress(Math.min(100, currentProgress))
      }, 120)
    }, delay)

    return () => {
      clearTimeout(startTimer)
      if (tick) clearInterval(tick)
    }
  }, [delay])

  return { stageIndex, progress, done }
}

export default function AnalyzingView({
  videoFiles,
  onComplete,
  error,
  isResolved = false,
  onRetry,
  onCancel,
}: AnalyzingViewProps) {
  const [aDone, setADone] = useState(false)
  const [bDone, setBDone] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  const panelA = usePanelProgress(0, () => setADone(true), isResolved)
  const panelB = usePanelProgress(500, () => setBDone(true), isResolved)

  useEffect(() => {
    if (aDone && bDone) {
      const t = setTimeout(() => onCompleteRef.current(), 800)
      return () => clearTimeout(t)
    }
  }, [aDone, bDone])

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-8">
      {/* Header */}
      <motion.div
        className="text-center mb-12"
        initial={{ opacity: 0, y: -16 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className="inline-flex items-center gap-2 mb-4 px-3 py-1 rounded-full text-xs tracking-widest uppercase"
          style={{
            background: error ? "rgba(255,107,107,0.08)" : "rgba(124,156,255,0.08)",
            border: error ? "1px solid rgba(255,107,107,0.25)" : "1px solid rgba(124,156,255,0.2)",
            color: error ? "#FF6B6B" : "#7C9CFF",
          }}>
          <motion.div
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: error ? "#FF6B6B" : "#7C9CFF" }}
            animate={{ opacity: error ? [1, 1, 1] : [1, 0.3, 1] }}
            transition={{ duration: 1, repeat: Infinity }}
          />
          {error ? "Failed" : "Processing"}
        </div>
        <h2 className="text-2xl font-light" style={{ color: "#e8eaf0" }}>
          {error ? "Analysis failed" : "Neural Analysis Running"}
        </h2>
        <p className="text-sm mt-2" style={{ color: "rgba(255,255,255,0.4)" }}>
          {error
            ? "The backend rejected this run. Details below."
            : "Both sessions are being processed in parallel through TRIBE v2"}
        </p>
      </motion.div>

      {/* Error panel */}
      {error && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-3xl mb-8 p-5 rounded-2xl"
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
                  background: "rgba(124,156,255,0.15)",
                  border: "1px solid rgba(124,156,255,0.3)",
                  color: "#7C9CFF",
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
      )}

      {/* Panels */}
      {!error && (
        <div className="flex gap-6 w-full max-w-3xl">
          <AnalyzingPanel version="A" videoFile={videoFiles.a} state={panelA} isResolved={isResolved} />
          <AnalyzingPanel version="B" videoFile={videoFiles.b} state={panelB} isResolved={isResolved} />
        </div>
      )}

      {!error && (
        <motion.p
          className="mt-10 text-xs"
          style={{ color: "rgba(255,255,255,0.2)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
        >
          {isResolved
            ? "Response received — finalizing UI"
            : "Pipeline takes 12–25s end-to-end (TRIBE GPU + Gemini)"}
        </motion.p>
      )}
    </div>
  )
}

interface AnalyzingPanelProps {
  version: "A" | "B"
  videoFile: File | null
  state: PanelState
  isResolved: boolean
}

function AnalyzingPanel({ version, videoFile, state, isResolved }: AnalyzingPanelProps) {
  const accent = version === "A" ? "#7C9CFF" : "#5CF2C5"
  const { stageIndex, progress, done } = state
  const videoUrlRef = useRef<string | null>(null)

  if (videoFile && !videoUrlRef.current) {
    videoUrlRef.current = URL.createObjectURL(videoFile)
  }

  const overallProgress = done
    ? 100
    : ((stageIndex * 100 + progress) / (STAGES.length * 100)) * 100

  return (
    <motion.div
      className="flex-1 panel rounded-2xl p-6 flex flex-col gap-5"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: version === "B" ? 0.15 : 0 }}
    >
      {/* Version badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold"
            style={{ background: `${accent}18`, color: accent }}>
            {version}
          </div>
          <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Version {version}</span>
        </div>
        {done && (
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            className="flex items-center gap-1.5 text-xs"
            style={{ color: "#5CF2C5" }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M20 6L9 17l-5-5" />
            </svg>
            Complete
          </motion.div>
        )}
      </div>

      {/* Video thumbnail */}
      <div className="relative rounded-xl overflow-hidden aspect-video"
        style={{ background: "rgba(0,0,0,0.4)", border: "1px solid rgba(255,255,255,0.06)" }}>
        {videoUrlRef.current ? (
          <video src={videoUrlRef.current} className="w-full h-full object-cover opacity-60" muted preload="metadata" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="text-4xl font-light" style={{ color: `${accent}25` }}>{version}</span>
          </div>
        )}

        {!done && (
          <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(11,15,20,0.4)" }}>
            <motion.div
              className="w-10 h-10 rounded-full"
              style={{ border: `2px solid ${accent}30`, borderTopColor: accent }}
              animate={{ rotate: 360 }}
              transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
            />
          </div>
        )}
      </div>

      {/* Stage text */}
      <div className="flex flex-col gap-3">
        <AnimatePresence mode="wait">
          <motion.p
            key={done ? "done" : stageIndex}
            className="text-sm font-medium"
            style={{ color: done ? "#5CF2C5" : accent }}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
          >
            {done
              ? "Analysis complete"
              : !isResolved && stageIndex === STAGES.length - 1
                ? `${STAGES[stageIndex]} (waiting on Gemini…)`
                : STAGES[stageIndex]}
          </motion.p>
        </AnimatePresence>

        {/* Stage progress bars */}
        <div className="flex gap-2">
          {STAGES.map((_, i) => (
            <div
              key={i}
              className="flex-1 h-0.5 rounded-full overflow-hidden"
              style={{ background: "rgba(255,255,255,0.08)" }}
            >
              <motion.div
                className="h-full rounded-full"
                style={{ background: accent }}
                initial={{ width: "0%" }}
                animate={{
                  width: done || i < stageIndex ? "100%" : i === stageIndex ? `${progress}%` : "0%",
                }}
                transition={{ duration: 0.1 }}
              />
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between">
          <p className="text-xs" style={{ color: "rgba(255,255,255,0.3)" }}>
            {done ? "Stage 4/4" : `Stage ${stageIndex + 1}/4`}
          </p>
          <p className="text-xs font-mono" style={{ color: accent }}>
            {overallProgress.toFixed(0)}%
          </p>
        </div>
      </div>
    </motion.div>
  )
}
