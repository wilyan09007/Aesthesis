"use client"

import { useState, useMemo, lazy, Suspense } from "react"
import { motion } from "framer-motion"
import VideoPlayerSynced from "./VideoPlayerSynced"
import BrainChart from "./BrainChart"
import InsightCard from "./InsightCard"
import VerdictPanel from "./VerdictPanel"
import type { Frame, ROIValues, VideoFiles } from "@/lib/types"
import type { ResultsViewData } from "@/lib/adapt"

const Brain3D = lazy(() => import("./Brain3D"))

interface ResultsViewProps {
  data: ResultsViewData
  videoFiles: VideoFiles
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

export default function ResultsView({ data, videoFiles, onReset }: ResultsViewProps) {
  const [currentTime, setCurrentTime] = useState(0)
  const [activeVersion, setActiveVersion] = useState<"A" | "B">("A")

  const activeFrames = activeVersion === "A" ? data.a.frames : data.b.frames
  const currentROI = useMemo(() => getCurrentROI(activeFrames, currentTime), [activeFrames, currentTime])

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
          {/* Top: video players + brain */}
          <motion.section
            className="flex gap-5 items-stretch"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <VideoPlayerSynced
              version="A"
              file={videoFiles.a}
              currentTime={currentTime}
              onTimeUpdate={handleSeek}
              isPrimary={true}
            />

            {/* Brain 3D centerpiece */}
            <div className="flex flex-col items-center justify-center gap-3 shrink-0">
              <Suspense fallback={<BrainFallback />}>
                <Brain3D roiValues={currentROI} size={220} />
              </Suspense>
              {/* Version toggle */}
              <div className="flex rounded-lg overflow-hidden text-xs"
                style={{ border: "1px solid rgba(255,255,255,0.08)" }}>
                {(["A", "B"] as const).map((v) => (
                  <button key={v} onClick={() => setActiveVersion(v)}
                    className="px-3 py-1.5 transition-all"
                    style={{
                      background: activeVersion === v ? "rgba(124,156,255,0.15)" : "transparent",
                      color: activeVersion === v ? "#7C9CFF" : "rgba(255,255,255,0.3)",
                      borderRight: v === "A" ? "1px solid rgba(255,255,255,0.08)" : "none",
                    }}>
                    {v}
                  </button>
                ))}
              </div>
            </div>

            <VideoPlayerSynced
              version="B"
              file={videoFiles.b}
              currentTime={currentTime}
              onTimeUpdate={() => {}}
              isPrimary={false}
            />
          </motion.section>

          {/* Middle: Brain chart */}
          <motion.section
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <BrainChart
              framesA={data.a.frames}
              framesB={data.b.frames}
              insightsA={data.a.insights}
              insightsB={data.b.insights}
              currentTime={currentTime}
              onSeek={handleSeek}
            />
          </motion.section>

          {/* Bottom: Insights + Verdict — secondary reference */}
          <motion.section
            className="grid grid-cols-3 gap-6"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
          >
            {/* Insights A */}
            <div className="panel rounded-2xl p-5 flex flex-col gap-4" style={{ opacity: 0.85 }}>
              <div className="flex items-center gap-2">
                <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold"
                  style={{ background: "rgba(124,156,255,0.15)", color: "#7C9CFF" }}>A</div>
                <h3 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Insights — A</h3>
                <span className="ml-auto text-[10px] tracking-wide" style={{ color: "rgba(255,255,255,0.2)" }}>reference</span>
              </div>
              <div className="flex flex-col gap-3">
                {data.a.insights.map((insight, i) => (
                  <InsightCard key={i} insight={insight} index={i} version="A" onSeek={handleSeek} />
                ))}
              </div>
            </div>

            {/* Insights B */}
            <div className="panel rounded-2xl p-5 flex flex-col gap-4" style={{ opacity: 0.85 }}>
              <div className="flex items-center gap-2">
                <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold"
                  style={{ background: "rgba(92,242,197,0.15)", color: "#5CF2C5" }}>B</div>
                <h3 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Insights — B</h3>
                <span className="ml-auto text-[10px] tracking-wide" style={{ color: "rgba(255,255,255,0.2)" }}>reference</span>
              </div>
              <div className="flex flex-col gap-3">
                {data.b.insights.map((insight, i) => (
                  <InsightCard key={i} insight={insight} index={i} version="B" onSeek={handleSeek} />
                ))}
              </div>
            </div>

            {/* Verdict */}
            <div>
              <VerdictPanel winner={data.verdict.winner} summary={data.verdict.summary} />
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
