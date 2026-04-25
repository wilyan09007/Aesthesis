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
  framesA: Frame[]
  framesB: Frame[]
  insightsA?: Insight[]
  insightsB?: Insight[]
  currentTime: number
  onSeek: (t: number) => void
}

type ChartVersion = "A" | "B" | "both"

interface ChartDataPoint {
  t_s: number
  [key: string]: number
}

type InsightEntry = { insight: Insight; v: "A" | "B" }

function buildChartData(frames: Frame[], prefix: string): ChartDataPoint[] {
  return frames.map((f) => {
    const point: ChartDataPoint = { t_s: f.t_s }
    for (const key of ROI_KEYS) {
      point[`${prefix}_${key}`] = f.values[key]
    }
    return point
  })
}

function mergeChartData(framesA: Frame[], framesB: Frame[]): ChartDataPoint[] {
  const mapB = new Map(framesB.map((f) => [f.t_s, f]))
  return framesA.map((fa) => {
    const fb = mapB.get(fa.t_s)
    const point: ChartDataPoint = { t_s: fa.t_s }
    for (const key of ROI_KEYS) {
      point[`A_${key}`] = fa.values[key]
      if (fb) point[`B_${key}`] = fb.values[key]
    }
    return point
  })
}

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
              <span style={{ color: "rgba(255,255,255,0.6)" }}>{entry.name.replace(/^[AB]_/, "")}</span>
            </div>
            <span className="font-mono" style={{ color: entry.color }}>{entry.value.toFixed(3)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function InsightPopover({ entry, containerWidth, maxTime, onSeek }: {
  entry: InsightEntry
  containerWidth: number
  maxTime: number
  onSeek: (t: number) => void
}) {
  const { insight, v } = entry
  const [t0, t1] = insight.timestamp_range_s
  const accent = v === "A" ? "#7C9CFF" : "#5CF2C5"
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
          border: `1px solid ${accent}40`,
          backdropFilter: "blur(20px)",
          boxShadow: `0 12px 40px rgba(0,0,0,0.6), 0 0 0 1px ${accent}10`,
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wide"
            style={{ background: `${accent}18`, color: accent }}>
            V{v}
          </span>
          <span className="font-mono" style={{ color: "rgba(255,255,255,0.38)" }}>
            {t0.toFixed(1)}s – {t1.toFixed(1)}s
          </span>
        </div>

        <p className="leading-relaxed mb-3" style={{ color: "rgba(255,255,255,0.78)" }}>
          {insight.ux_observation}
        </p>

        <div className="flex items-start gap-2 pt-2.5" style={{ borderTop: `1px solid ${accent}20` }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={accent} strokeWidth="2.5" className="shrink-0 mt-0.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
          <p className="leading-relaxed" style={{ color: accent }}>
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

export default function BrainChart({ framesA, framesB, insightsA, insightsB, currentTime, onSeek }: BrainChartProps) {
  const [version, setVersion] = useState<ChartVersion>("A")
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

  const chartData = version === "both"
    ? mergeChartData(framesA, framesB)
    : version === "A"
      ? buildChartData(framesA, "A")
      : buildChartData(framesB, "B")

  const maxTime = framesA.length > 0 ? framesA[framesA.length - 1].t_s : 0

  const visibleInsights: InsightEntry[] = [
    ...(insightsA && (version === "A" || version === "both")
      ? insightsA.map(ins => ({ insight: ins, v: "A" as const }))
      : []),
    ...(insightsB && (version === "B" || version === "both")
      ? insightsB.map(ins => ({ insight: ins, v: "B" as const }))
      : []),
  ]

  const activeInsightEntry = mouseTime !== null
    ? (visibleInsights.find(
        ({ insight }) => mouseTime >= insight.timestamp_range_s[0] && mouseTime <= insight.timestamp_range_s[1]
      ) ?? null)
    : null

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleClick = useCallback((data: any) => {
    const t = data?.activePayload?.[0]?.payload?.t_s as number | undefined
    if (t === undefined) return
    const hit = visibleInsights.find(
      ({ insight }) => t >= insight.timestamp_range_s[0] && t <= insight.timestamp_range_s[1]
    )
    onSeek(hit ? hit.insight.timestamp_range_s[0] : t)
  }, [onSeek, visibleInsights])

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleMouseMove = useCallback((data: any) => {
    const t = data?.activePayload?.[0]?.payload?.t_s
    setMouseTime(typeof t === "number" ? t : null)
  }, [])

  const prefixes = version === "both" ? ["A", "B"] : [version]

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

        <div className="flex rounded-lg overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.08)" }}>
          {(["A", "B", "both"] as ChartVersion[]).map((v) => (
            <button
              key={v}
              onClick={() => setVersion(v)}
              className="px-3 py-1.5 text-xs font-medium transition-all"
              style={{
                background: version === v ? "rgba(124,156,255,0.15)" : "transparent",
                color: version === v ? "#7C9CFF" : "rgba(255,255,255,0.35)",
                borderRight: v !== "both" ? "1px solid rgba(255,255,255,0.08)" : "none",
              }}
            >
              {v === "both" ? "A+B" : `Version ${v}`}
            </button>
          ))}
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
            style={{ cursor: activeInsightEntry ? "pointer" : "crosshair" }}
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
            <Tooltip content={activeInsightEntry ? () => null : <ROITooltip />} />
            <ReferenceLine
              x={currentTime}
              stroke="rgba(255,255,255,0.5)"
              strokeWidth={1}
              strokeDasharray="4 4"
            />

            {/* Insight range bands */}
            {visibleInsights.map(({ insight, v }, i) => {
              const isActive = activeInsightEntry?.insight === insight
              return (
                <ReferenceArea
                  key={`ins_${v}_${i}`}
                  x1={insight.timestamp_range_s[0]}
                  x2={insight.timestamp_range_s[1]}
                  fill={
                    isActive
                      ? v === "A" ? "rgba(124,156,255,0.2)" : "rgba(92,242,197,0.15)"
                      : v === "A" ? "rgba(124,156,255,0.07)" : "rgba(92,242,197,0.05)"
                  }
                  stroke={v === "A" ? "rgba(124,156,255,0.25)" : "rgba(92,242,197,0.2)"}
                  strokeWidth={isActive ? 1 : 0}
                  strokeDasharray="2 2"
                  ifOverflow="hidden"
                />
              )
            })}

            {prefixes.flatMap((prefix) =>
              ROI_KEYS.map((key) => (
                <Line
                  key={`${prefix}_${key}`}
                  type="monotone"
                  dataKey={`${prefix}_${key}`}
                  name={`${prefix}_${ROI_LABELS[key]}`}
                  stroke={ROI_COLORS[key]}
                  strokeWidth={activeKey === key ? 2 : 1.5}
                  strokeDasharray={prefix === "B" && version === "both" ? "4 2" : undefined}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  opacity={activeKey === null || activeKey === key ? 1 : 0.2}
                  isAnimationActive={false}
                />
              ))
            )}
          </LineChart>
        </ResponsiveContainer>

        {/* Insight popover — appears above chart at insight midpoint */}
        <AnimatePresence>
          {activeInsightEntry && containerWidth > 0 && (
            <InsightPopover
              key={`${activeInsightEntry.v}_${activeInsightEntry.insight.timestamp_range_s[0]}`}
              entry={activeInsightEntry}
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
