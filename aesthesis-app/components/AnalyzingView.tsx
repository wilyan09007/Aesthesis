"use client"

import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { VideoFiles } from "@/lib/types"

interface AnalyzingViewProps {
  videoFiles: VideoFiles
  onComplete: () => void
}

const STAGES = [
  "Encoding neural features…",
  "Mapping cortical response…",
  "Extracting ROI signals…",
  "Generating insights…",
]

type PanelState = {
  stageIndex: number
  progress: number
  done: boolean
}

function usePanelProgress(delay: number, onDone: () => void): PanelState {
  const [stageIndex, setStageIndex] = useState(0)
  const [progress, setProgress] = useState(0)
  const [done, setDone] = useState(false)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    let tick: ReturnType<typeof setInterval> | null = null

    const startTimer = setTimeout(() => {
      let currentStage = 0
      let currentProgress = 0

      tick = setInterval(() => {
        currentProgress += Math.random() * 8 + 4

        if (currentProgress >= 100) {
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

export default function AnalyzingView({ videoFiles, onComplete }: AnalyzingViewProps) {
  const [aDone, setADone] = useState(false)
  const [bDone, setBDone] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  const panelA = usePanelProgress(0, () => setADone(true))
  const panelB = usePanelProgress(500, () => setBDone(true))

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
          style={{ background: "rgba(124,156,255,0.08)", border: "1px solid rgba(124,156,255,0.2)", color: "#7C9CFF" }}>
          <motion.div
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: "#7C9CFF" }}
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 1, repeat: Infinity }}
          />
          Processing
        </div>
        <h2 className="text-2xl font-light" style={{ color: "#e8eaf0" }}>Neural Analysis Running</h2>
        <p className="text-sm mt-2" style={{ color: "rgba(255,255,255,0.4)" }}>
          Both sessions are being processed in parallel through TRIBE v2
        </p>
      </motion.div>

      {/* Panels */}
      <div className="flex gap-6 w-full max-w-3xl">
        <AnalyzingPanel version="A" videoFile={videoFiles.a} state={panelA} />
        <AnalyzingPanel version="B" videoFile={videoFiles.b} state={panelB} />
      </div>

      <motion.p
        className="mt-10 text-xs"
        style={{ color: "rgba(255,255,255,0.2)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
      >
        Each panel updates independently as stages complete
      </motion.p>
    </div>
  )
}

interface AnalyzingPanelProps {
  version: "A" | "B"
  videoFile: File | null
  state: PanelState
}

function AnalyzingPanel({ version, videoFile, state }: AnalyzingPanelProps) {
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
            {done ? "Analysis complete" : STAGES[stageIndex]}
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
