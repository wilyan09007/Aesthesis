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
  // D11 — Phase 2 capture path: show a "Captured. Analyzing in 3...2...1"
  // gate before kicking off the analyze call. Cheap insurance against
  // burning ~55-75s of TRIBE+Gemini compute on garbage captures (login
  // walls, cookie modals). Skip-path callers leave both undefined and
  // get the original immediate behavior.
  confirmCountdownS?: number
  onConfirm?: () => void
  onConfirmCancel?: () => void
}

const STAGES = [
  "Encoding neural features…",
  "Mapping cortical response…",
  "Extracting ROI signals…",
  "Generating insights…",
]

const HOLD_AT_PCT = 95 // hold the last stage at this % until the response arrives

const ACCENT = "#7C9CFF"

/** Tiny video thumbnail used inside the D11 confirm-countdown panel. */
function CountdownVideoThumb({ file }: { file: File | null }) {
  const urlRef = useRef<string | null>(null)
  if (file && !urlRef.current) {
    urlRef.current = URL.createObjectURL(file)
  }
  if (!urlRef.current) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <span className="text-4xl font-light" style={{ color: `${ACCENT}25` }}>·</span>
      </div>
    )
  }
  return (
    <video
      src={urlRef.current}
      className="w-full h-full object-cover opacity-70"
      muted
      autoPlay
      loop
      playsInline
      preload="metadata"
    />
  )
}

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
  videoFile,
  onComplete,
  error,
  isResolved = false,
  onRetry,
  onCancel,
  confirmCountdownS,
  onConfirm,
  onConfirmCancel,
}: AnalyzingViewProps) {
  const [done, setDone] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  // D11 — confirm gate before kicking off the analyze call (capture path)
  const [gatePassed, setGatePassed] = useState<boolean>(confirmCountdownS == null)
  const [secondsLeft, setSecondsLeft] = useState<number>(confirmCountdownS ?? 0)
  const onConfirmRef = useRef(onConfirm)
  onConfirmRef.current = onConfirm

  useEffect(() => {
    if (gatePassed || confirmCountdownS == null) return
    if (secondsLeft <= 0) {
      setGatePassed(true)
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] confirm_countdown_complete -> firing analyze")
      onConfirmRef.current?.()
      return
    }
    const t = setTimeout(() => setSecondsLeft((s) => s - 1), 1000)
    return () => clearTimeout(t)
  }, [secondsLeft, gatePassed, confirmCountdownS])

  // Don't start the progress animation until the gate has passed.
  // usePanelProgress relies on a stable delay value across renders, so
  // we conditionally short-circuit by passing a far-future delay until
  // we're past the gate. (Safer than a conditional hook call.)
  const progressDelay = gatePassed ? 0 : 60_000_000
  const panel = usePanelProgress(progressDelay, () => setDone(true), isResolved)

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
          {error
            ? "Analysis failed"
            : confirmCountdownS != null && !gatePassed
              ? "Captured"
              : "Neural Analysis Running"}
        </h2>
        <p className="text-sm mt-2" style={{ color: "rgba(255,255,255,0.4)" }}>
          {error
            ? "The backend rejected this run. Details below."
            : confirmCountdownS != null && !gatePassed
              ? `Analyzing in ${secondsLeft}…`
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

      {/* Confirm-countdown panel (D11 capture path) — replaces the
          progress panel for the first 3s so the user sees the captured
          video and can cancel if it's obviously garbage (login wall,
          cookie modal, wrong page). */}
      {!error && confirmCountdownS != null && !gatePassed && (
        <motion.div
          className="w-full max-w-2xl"
          initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
        >
          <div className="panel rounded-2xl p-6 flex flex-col gap-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full" style={{ background: ACCENT }} />
                <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Confirm capture</span>
              </div>
              <span className="text-xs font-mono" style={{ color: ACCENT }}>{secondsLeft}s</span>
            </div>

            <div className="relative rounded-xl overflow-hidden aspect-video"
              style={{ background: "rgba(0,0,0,0.4)", border: "1px solid rgba(255,255,255,0.06)" }}>
              <CountdownVideoThumb file={videoFile} />
              <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(11,15,20,0.45)" }}>
                <div className="text-center">
                  <p className="text-5xl font-light tabular-nums" style={{ color: ACCENT }}>{secondsLeft}</p>
                  <p className="text-xs mt-2" style={{ color: "rgba(255,255,255,0.45)" }}>Analyzing in…</p>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <p className="text-xs flex-1" style={{ color: "rgba(255,255,255,0.45)" }}>
                If the captured video looks wrong (login wall, cookie modal, wrong page),
                cancel now and try again before the analysis fires.
              </p>
              {onConfirmCancel && (
                <button
                  onClick={onConfirmCancel}
                  className="px-4 py-2 rounded-lg text-xs font-medium"
                  style={{
                    background: "rgba(255,107,107,0.1)",
                    border: "1px solid rgba(255,107,107,0.3)",
                    color: "#FF6B6B",
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              )}
            </div>
          </div>
        </motion.div>
      )}

      {/* Panel — the original analyzing UI, shown after the gate passes
          (or immediately on the skip path where confirmCountdownS is undefined). */}
      {!error && (confirmCountdownS == null || gatePassed) && (
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
            : "Pipeline takes ~6–13s end-to-end (TRIBE GPU + Gemini)"}
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
            {done ? `Stage ${STAGES.length}/${STAGES.length}` : `Stage ${stageIndex + 1}/${STAGES.length}`}
          </p>
          <p className="text-xs font-mono" style={{ color: ACCENT }}>
            {overallProgress.toFixed(0)}%
          </p>
        </div>
      </div>
    </motion.div>
  )
}
