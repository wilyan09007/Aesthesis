"use client"

import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"

interface AnalyzingViewProps {
  videoFile: File | null
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

// Stage-start thresholds on the 0-MAX_PRE_ARRIVAL curve below. Stage 4
// (Gemini) is the bulk of real runs, so we let it span the largest range.
const STAGE_THRESHOLDS = [0, 17, 42, 64] as const

// Visible cap before the backend responds. Sits clearly below 100% so the
// bar can never look "finished" while we're still waiting on the response.
const MAX_PRE_ARRIVAL = 92

// Exponential time constant (ms). Calibrated against an observed end-to-end
// pipeline time of ~165s (TRIBE GPU + Gemini): the bar reaches ~20% at 6s,
// ~37% at 13s, ~64% at 30s, ~84% at 60s, ~91% at 120s, ~92% (cap) by ~150s.
const TIME_CONSTANT = 25000

// Past this elapsed time we stop estimating and switch the message to a
// clearly indeterminate "taking longer than usual" state instead of letting
// the bar plateau silently.
const LONG_RUNNING_MS = 45000

type PanelState = {
  stageIndex: number
  progress: number  // 0-100 within current stage
  overall: number   // 0-MAX_PRE_ARRIVAL pre-arrival, 100 once resolved
  elapsed: number   // ms since start
  done: boolean
}

function usePanelProgress(onDone: () => void, isResolved: boolean): PanelState {
  const [stageIndex, setStageIndex] = useState(0)
  const [progress, setProgress] = useState(0)
  const [overall, setOverall] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [done, setDone] = useState(false)
  const startTime = useRef(Date.now())
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  // Tick: drive progress from wall-clock elapsed time
  useEffect(() => {
    const tick = setInterval(() => {
      const ms = Date.now() - startTime.current
      const naturalOverall = MAX_PRE_ARRIVAL * (1 - Math.exp(-ms / TIME_CONSTANT))

      // Which stage are we in?
      let stage = STAGES.length - 1
      for (let i = STAGE_THRESHOLDS.length - 1; i >= 0; i--) {
        if (naturalOverall >= STAGE_THRESHOLDS[i]) { stage = i; break }
      }

      // Progress within the current stage (0-100). The last stage's virtual
      // end is 100 (not MAX_PRE_ARRIVAL) so it never visually fills while
      // we're still waiting on the backend.
      const stageStart = STAGE_THRESHOLDS[stage]
      const stageEnd = stage < STAGE_THRESHOLDS.length - 1
        ? STAGE_THRESHOLDS[stage + 1]
        : 100
      const withinStage = stageEnd > stageStart
        ? Math.min(100, ((naturalOverall - stageStart) / (stageEnd - stageStart)) * 100)
        : 0

      setStageIndex(stage)
      setProgress(withinStage)
      setOverall(naturalOverall)
      setElapsed(ms)
    }, 150)

    return () => clearInterval(tick)
  }, [])

  // When the backend responds, complete immediately
  useEffect(() => {
    if (!isResolved || done) return
    setStageIndex(STAGES.length - 1)
    setProgress(100)
    setOverall(100)
    const t = setTimeout(() => {
      setDone(true)
      onDoneRef.current()
    }, 500)
    return () => clearTimeout(t)
  }, [isResolved, done])

  return { stageIndex, progress, overall, elapsed, done }
}

export default function AnalyzingView({
  videoFile,
  onComplete,
  error,
  isResolved = false,
  onRetry,
  onCancel,
}: AnalyzingViewProps) {
  const [done, setDone] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  const panel = usePanelProgress(() => setDone(true), isResolved)

  useEffect(() => {
    if (done) {
      const t = setTimeout(() => onCompleteRef.current(), 600)
      return () => clearTimeout(t)
    }
  }, [done])

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
            : "Reading the demo through TRIBE v2"}
        </p>
      </motion.div>

      {/* Error panel */}
      {error && (
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

      {/* Panel */}
      {!error && (
        <div className="w-full max-w-2xl">
          <AnalyzingPanel videoFile={videoFile} state={panel} isResolved={isResolved} />
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
            : "Pipeline typically takes 1–3 minutes (TRIBE GPU + Gemini)"}
        </motion.p>
      )}
    </div>
  )
}

interface AnalyzingPanelProps {
  videoFile: File | null
  state: PanelState
  isResolved: boolean
}

const ACCENT = "#7C9CFF"

function AnalyzingPanel({ videoFile, state, isResolved }: AnalyzingPanelProps) {
  const { stageIndex, progress, overall, elapsed, done } = state
  const videoUrlRef = useRef<string | null>(null)

  if (videoFile && !videoUrlRef.current) {
    videoUrlRef.current = URL.createObjectURL(videoFile)
  }

  // Use the time-based curve directly: it's already capped at MAX_PRE_ARRIVAL,
  // so the bar can't reach 99% before the backend actually responds.
  const overallProgress = (done || isResolved) ? 100 : Math.min(overall, MAX_PRE_ARRIVAL)
  const longRunning = !done && !isResolved && elapsed > LONG_RUNNING_MS
  const elapsedSec = Math.floor(elapsed / 1000)

  return (
    <motion.div
      className="panel rounded-2xl p-6 flex flex-col gap-5"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ background: ACCENT }} />
          <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Analyzing demo</span>
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
            <span className="text-4xl font-light" style={{ color: `${ACCENT}25` }}>·</span>
          </div>
        )}

        {!done && (
          <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(11,15,20,0.4)" }}>
            <motion.div
              className="w-10 h-10 rounded-full"
              style={{ border: `2px solid ${ACCENT}30`, borderTopColor: ACCENT }}
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
            style={{ color: done ? "#5CF2C5" : ACCENT }}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
          >
            {done
              ? "Analysis complete"
              : longRunning
                ? `${STAGES[stageIndex]} · taking longer than usual…`
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
                style={{ background: ACCENT }}
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
            {done
              ? `Stage ${STAGES.length}/${STAGES.length} · ${elapsedSec}s`
              : `Stage ${stageIndex + 1}/${STAGES.length} · ${elapsedSec}s elapsed`}
          </p>
          <p className="text-xs font-mono" style={{ color: ACCENT }}>
            {overallProgress.toFixed(0)}%
          </p>
        </div>
      </div>
    </motion.div>
  )
}
