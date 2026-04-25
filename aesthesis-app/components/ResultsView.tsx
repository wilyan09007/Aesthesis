"use client"

import { useState, useMemo, lazy, Suspense } from "react"
import { motion } from "framer-motion"
import VideoPlayer from "./VideoPlayer"
import BrainChart from "./BrainChart"
import InsightCard from "./InsightCard"
import OverallAssessmentPanel from "./OverallAssessmentPanel"
import type { Frame, ROIValues } from "@/lib/types"
import type { ResultsViewData } from "@/lib/adapt"

const Brain3D = lazy(() => import("./Brain3D"))

interface ResultsViewProps {
  data: ResultsViewData
  videoFile: File | null
  onReset: () => void
}

function getCurrentROI(frames: Frame[], currentTime: number): ROIValues | undefined {
  if (!frames.length) return undefined
  let closest = frames[0]
  for (const f of frames) {
    if (Math.abs(f.t_s - currentTime) < Math.abs(closest.t_s - currentTime)) {
      closest = f
    }
  }
  return closest.values
}

export default function ResultsView({ data, videoFile, onReset }: ResultsViewProps) {
  const [currentTime, setCurrentTime] = useState(0)
  const currentROI = useMemo(() => getCurrentROI(data.frames, currentTime), [data.frames, currentTime])

  const handleSeek = (t: number) => setCurrentTime(t)

  return (
    <div className="min-h-screen flex flex-col">
      {/* Nav */}
      <div className="flex items-center justify-between px-8 py-4 shrink-0"
        style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-full flex items-center justify-center"
            style={{ background: "rgba(124,156,255,0.1)", border: "1px solid rgba(124,156,255,0.2)" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#7C9CFF" strokeWidth="1.5">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
          </div>
          <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Aesthesis</span>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: "rgba(92,242,197,0.1)", color: "#5CF2C5", border: "1px solid rgba(92,242,197,0.2)" }}>
            Results
          </span>
        </div>

        <button
          onClick={onReset}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs transition-colors"
          style={{ color: "rgba(255,255,255,0.4)", border: "1px solid rgba(255,255,255,0.08)" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
          onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.4)")}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 12a9 9 0 109-9 9.75 9.75 0 00-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
          </svg>
          New Analysis
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-8 py-8 flex flex-col gap-8">
          {/* Top: video player + 3D brain */}
          <motion.section
            className="flex gap-5 items-stretch"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <VideoPlayer
              file={videoFile}
              currentTime={currentTime}
              onTimeUpdate={handleSeek}
            />

            {/* Brain 3D centerpiece */}
            <div className="flex flex-col items-center justify-center gap-3 shrink-0">
              <Suspense fallback={<BrainFallback />}>
                <Brain3D roiValues={currentROI} size={220} />
              </Suspense>
              <p className="text-xs" style={{ color: "rgba(255,255,255,0.35)" }}>
                Neural state at {currentTime.toFixed(1)}s
              </p>
            </div>
          </motion.section>

          {/* Middle: Brain chart */}
          <motion.section
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <BrainChart
              frames={data.frames}
              insights={data.insights}
              currentTime={currentTime}
              onSeek={handleSeek}
            />
          </motion.section>

          {/* Bottom: Insights + Assessment */}
          <motion.section
            className="grid grid-cols-3 gap-6"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
          >
            {/* Insight cards — 2 cols */}
            <div className="col-span-2 panel rounded-2xl p-5 flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full" style={{ background: "#7C9CFF" }} />
                <h3 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Timestamped insights</h3>
                <span className="ml-auto text-[10px] tracking-wide" style={{ color: "rgba(255,255,255,0.3)" }}>
                  {data.insights.length} moment{data.insights.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {data.insights.map((insight, i) => (
                  <InsightCard key={i} insight={insight} index={i} onSeek={handleSeek} />
                ))}
                {data.insights.length === 0 && (
                  <p className="text-xs col-span-2" style={{ color: "rgba(255,255,255,0.35)" }}>
                    No notable moments detected. The demo may be too short or too uniform.
                  </p>
                )}
              </div>
            </div>

            {/* Assessment panel — 1 col */}
            <div>
              <OverallAssessmentPanel assessment={data.assessment} />
            </div>
          </motion.section>
        </div>
      </div>
    </div>
  )
}

function BrainFallback() {
  return (
    <div className="flex items-center justify-center" style={{ width: 220, height: 220 }}>
      <motion.div
        className="w-16 h-16 rounded-full"
        style={{ border: "2px solid rgba(124,156,255,0.3)", borderTopColor: "#7C9CFF" }}
        animate={{ rotate: 360 }}
        transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}
      />
    </div>
  )
}
