"use client"

import { useState, useMemo, lazy, Suspense } from "react"
import { motion } from "framer-motion"
import VideoPlayer from "./VideoPlayer"
import BrainChart from "./BrainChart"
import InsightCard from "./InsightCard"
import OverallAssessmentPanel from "./OverallAssessmentPanel"
import type { Frame, ROIValues } from "@/lib/types"
import type { ResultsViewData } from "@/lib/adapt"

// BrainCortical is the real cortical mesh visualization. It internally
// falls back to Brain3D when `parcelSeries` is null (e.g., the TRIBE
// worker hasn't been re-baked with the parcel map). ASSUMPTIONS_BRAIN.md §3.6.
const BrainCortical = lazy(() => import("./BrainCortical"))

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

  // Map currentTime → TR index for the cortical brain. Clamped to the
  // valid range so an out-of-range scrub (e.g., user drags past video
  // end mid-load) doesn't paint garbage.
  // ASSUMPTIONS_BRAIN.md §3.4.
  const tIndex = useMemo(() => {
    const tr = data.tr_duration_s || 1.5
    const nTRs = data.parcel_series?.length ?? 0
    if (nTRs === 0) return 0
    const raw = Math.floor(currentTime / tr)
    return Math.max(0, Math.min(nTRs - 1, raw))
  }, [currentTime, data.tr_duration_s, data.parcel_series])

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
          {/* Top: video player + 3D brain — paired, equal-height panels. */}
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

            {/* Brain 3D — its own panel, square, drag-to-rotate. */}
            <div
              className="rounded-xl overflow-hidden panel flex flex-col shrink-0"
              style={{ width: "50vh", height: "50vh", maxWidth: "50vh" }}
            >
              <div
                className="flex items-center gap-2 px-4 py-3 shrink-0"
                style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
              >
                <div className="w-2 h-2 rounded-full" style={{ background: "#5CF2C5" }} />
                <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>
                  Neural state
                </span>
                <span
                  className="ml-auto text-xs font-mono"
                  style={{ color: "rgba(255,255,255,0.35)" }}
                >
                  t = {currentTime.toFixed(1)}s
                </span>
              </div>
              <div className="relative flex-1 min-h-0 flex items-center justify-center">
                <Suspense fallback={<BrainFallback />}>
                  <BrainCortical
                    parcelSeries={data.parcel_series}
                    tIndex={tIndex}
                    roiValues={currentROI}
                  />
                </Suspense>
                <p
                  className="absolute bottom-2 left-0 right-0 text-center text-[10px] tracking-wide pointer-events-none"
                  style={{ color: "rgba(255,255,255,0.25)" }}
                >
                  drag to rotate
                </p>
              </div>
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

// Faint orb that hints at the brain coming in, instead of a generic spinner —
// avoids a layout pop when Brain3D's lazy chunk resolves.
function BrainFallback() {
  return (
    <div className="w-full h-full flex items-center justify-center">
      <motion.div
        className="rounded-full"
        style={{
          width: "60%",
          aspectRatio: "1 / 1",
          background:
            "radial-gradient(circle at 35% 35%, rgba(124,156,255,0.18), rgba(124,156,255,0.04) 60%, transparent 75%)",
          border: "1px solid rgba(124,156,255,0.12)",
        }}
        animate={{ opacity: [0.5, 0.85, 0.5] }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  )
}
