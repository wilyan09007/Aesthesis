"use client"

import { useState, useCallback, useEffect, useRef } from "react"
import { motion, AnimatePresence } from "framer-motion"
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ReferenceArea,
  ResponsiveContainer,
} from "recharts"
import type { Frame, Insight } from "@/lib/types"
import { ROI_COLORS, ROI_LABELS, ROI_KEYS } from "@/lib/types"

// Matches the LineChart margin + YAxis width set below
const CHART_MARGIN_LEFT = 5
const CHART_MARGIN_RIGHT = 8
const Y_AXIS_W = 32
const PLOT_LEFT = CHART_MARGIN_LEFT + Y_AXIS_W

interface BrainChartProps {
  frames: Frame[]
  insights?: Insight[]
  currentTime: number
  onSeek: (t: number) => void
}

interface ChartDataPoint {
  t_s: number
  [key: string]: number
}

function buildChartData(frames: Frame[]): ChartDataPoint[] {
  return frames.map((f) => {
    const point: ChartDataPoint = { t_s: f.t_s }
    for (const key of ROI_KEYS) {
      point[key] = f.values[key]
    }
    return point
  })
}

const ACCENT = "#7C9CFF"

const ROITooltip = ({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
}) => {
  if (!active || !payload?.length) return null
  return (
    <div className="p-3 rounded-xl text-xs"
      style={{ background: "rgba(11,15,20,0.95)", border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", minWidth: 200 }}>
      <p className="font-mono mb-2" style={{ color: "rgba(255,255,255,0.5)" }}>{label?.toFixed(1)}s</p>
      <div className="flex flex-col gap-1">
        {payload.slice(0, 8).map((entry) => (
          <div key={entry.name} className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full" style={{ background: entry.color }} />
              <span style={{ color: "rgba(255,255,255,0.6)" }}>{entry.name}</span>
            </div>
            <span className="font-mono" style={{ color: entry.color }}>{entry.value.toFixed(3)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function InsightPopover({ insight, containerWidth, maxTime, onSeek }: {
  insight: Insight
  containerWidth: number
  maxTime: number
  onSeek: (t: number) => void
}) {
  const [t0, t1] = insight.timestamp_range_s
  const plotWidth = containerWidth - PLOT_LEFT - CHART_MARGIN_RIGHT
  const midX = PLOT_LEFT + (((t0 + t1) / 2) / maxTime) * plotWidth
  const POPOVER_W = 320
  const left = Math.max(POPOVER_W / 2, Math.min(containerWidth - POPOVER_W / 2, midX))

  return (
    <motion.div
      className="absolute z-50 pointer-events-none"
      style={{ top: 10, left, transform: "translateX(-50%)", width: POPOVER_W }}
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.14 }}
    >
      <div
        className="rounded-xl p-4 text-xs"
        style={{
          background: "rgba(9,12,18,0.98)",
          border: `1px solid ${ACCENT}40`,
          backdropFilter: "blur(20px)",
          boxShadow: `0 12px 40px rgba(0,0,0,0.6), 0 0 0 1px ${ACCENT}10`,
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <span className="font-mono" style={{ color: "rgba(255,255,255,0.38)" }}>
            {t0.toFixed(1)}s – {t1.toFixed(1)}s
          </span>
        </div>

        <p className="leading-relaxed mb-3" style={{ color: "rgba(255,255,255,0.78)" }}>
          {insight.ux_observation}
        </p>

        <div className="flex items-start gap-2 pt-2.5" style={{ borderTop: `1px solid ${ACCENT}20` }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth="2.5" className="shrink-0 mt-0.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
          <p className="leading-relaxed" style={{ color: ACCENT }}>
            {insight.recommendation}
          </p>
        </div>

        <p className="mt-2.5 pt-2 text-[10px]" style={{ color: "rgba(255,255,255,0.22)", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
          Click chart to seek video to {t0.toFixed(1)}s
        </p>
      </div>
    </motion.div>
  )
}

export default function BrainChart({ frames, insights, currentTime, onSeek }: BrainChartProps) {
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [mouseTime, setMouseTime] = useState<number | null>(null)
  const [containerWidth, setContainerWidth] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const chartData = buildChartData(frames)
  const maxTime = frames.length > 0 ? frames[frames.length - 1].t_s : 0

  const visibleInsights = insights ?? []

  const activeInsight = mouseTime !== null
    ? (visibleInsights.find(
        (ins) => mouseTime >= ins.timestamp_range_s[0] && mouseTime <= ins.timestamp_range_s[1]
      ) ?? null)
    : null

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleClick = useCallback((data: any) => {
    const t = data?.activePayload?.[0]?.payload?.t_s as number | undefined
    if (t === undefined) return
    const hit = visibleInsights.find(
      (ins) => t >= ins.timestamp_range_s[0] && t <= ins.timestamp_range_s[1]
    )
    onSeek(hit ? hit.timestamp_range_s[0] : t)
  }, [onSeek, visibleInsights])

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleMouseMove = useCallback((data: any) => {
    const t = data?.activePayload?.[0]?.payload?.t_s
    setMouseTime(typeof t === "number" ? t : null)
  }, [])

  return (
    <div className="panel rounded-2xl p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Brain Timeline</h3>
          <p className="text-xs mt-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>
            8 ROI signals · hover shaded regions for insights · click to seek
          </p>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5 mb-4">
        {ROI_KEYS.map((key) => (
          <button
            key={key}
            className="flex items-center gap-1.5 text-xs transition-opacity"
            style={{ opacity: activeKey === null || activeKey === key ? 1 : 0.3 }}
            onMouseEnter={() => setActiveKey(key)}
            onMouseLeave={() => setActiveKey(null)}
          >
            <div className="w-2.5 h-0.5 rounded-full" style={{ background: ROI_COLORS[key] }} />
            <span style={{ color: "rgba(255,255,255,0.5)" }}>{ROI_LABELS[key]}</span>
          </button>
        ))}
      </div>

      {/* Chart wrapper — overflow:visible so popover can escape the panel boundary */}
      <div ref={containerRef} className="relative" style={{ overflow: "visible" }}>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart
            data={chartData}
            margin={{ top: 5, right: CHART_MARGIN_RIGHT, left: CHART_MARGIN_LEFT, bottom: 5 }}
            onClick={handleClick}
            onMouseMove={handleMouseMove}
            onMouseLeave={() => setMouseTime(null)}
            style={{ cursor: activeInsight ? "pointer" : "crosshair" }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
            <XAxis
              dataKey="t_s"
              type="number"
              domain={[0, maxTime]}
              tickFormatter={(v) => `${v}s`}
              tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 11 }}
              axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 1]}
              tickFormatter={(v) => v.toFixed(1)}
              tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={Y_AXIS_W}
            />
            <Tooltip content={activeInsight ? () => null : <ROITooltip />} />
            <ReferenceLine
              x={currentTime}
              stroke="rgba(255,255,255,0.5)"
              strokeWidth={1}
              strokeDasharray="4 4"
            />

            {/* Insight range bands */}
            {visibleInsights.map((insight, i) => {
              const isActive = activeInsight === insight
              return (
                <ReferenceArea
                  key={`ins_${i}`}
                  x1={insight.timestamp_range_s[0]}
                  x2={insight.timestamp_range_s[1]}
                  fill={isActive ? "rgba(124,156,255,0.2)" : "rgba(124,156,255,0.07)"}
                  stroke="rgba(124,156,255,0.25)"
                  strokeWidth={isActive ? 1 : 0}
                  strokeDasharray="2 2"
                  ifOverflow="hidden"
                />
              )
            })}

            {ROI_KEYS.map((key) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                name={ROI_LABELS[key]}
                stroke={ROI_COLORS[key]}
                strokeWidth={activeKey === key ? 2 : 1.5}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
                opacity={activeKey === null || activeKey === key ? 1 : 0.2}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>

        {/* Insight popover — appears above chart at insight midpoint */}
        <AnimatePresence>
          {activeInsight && containerWidth > 0 && (
            <InsightPopover
              key={activeInsight.timestamp_range_s[0]}
              insight={activeInsight}
              containerWidth={containerWidth}
              maxTime={maxTime}
              onSeek={onSeek}
            />
          )}
        </AnimatePresence>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-3 pt-3" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <p className="text-xs" style={{ color: "rgba(255,255,255,0.25)" }}>
          {visibleInsights.length > 0
            ? `${visibleInsights.length} insight region${visibleInsights.length !== 1 ? "s" : ""} overlaid`
            : "Click any point on the chart to seek video"}
        </p>
        <p className="text-xs font-mono" style={{ color: "rgba(255,255,255,0.4)" }}>
          {currentTime.toFixed(1)}s / {maxTime.toFixed(1)}s
        </p>
      </div>
    </div>
  )
}
