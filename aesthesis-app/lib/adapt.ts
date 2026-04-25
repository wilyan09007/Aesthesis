// Adapter: backend wire shape → view shape consumed by ResultsView.
//
// The backend ships TimelineSummary { roi_series: { roi_name: number[] }, tr_duration_s }
// because that's compact on the wire. The chart wants Frame[] = [{ t_s, values }].
// We adapt once on receipt; components stay backend-agnostic.
//
// Single-video pivot (DESIGN.md §17): no A/B split. The OverallAssessment
// replaces the legacy Verdict; the panel component just needs a `summary`
// string + bullets. Per-frame derivation is unchanged.

import type {
  AnalyzeResponse,
  Frame,
  Insight,
  OverallAssessment,
  ROIKey,
  ROIValues,
  TimelineSummary,
} from "./types"
import { ROI_KEYS } from "./types"

export function framesFromTimeline(timeline: TimelineSummary): Frame[] {
  const tr = timeline.tr_duration_s || 1.5
  const seriesByKey: Record<ROIKey, number[]> = ROI_KEYS.reduce(
    (acc, k) => {
      acc[k] = timeline.roi_series?.[k] ?? []
      return acc
    },
    {} as Record<ROIKey, number[]>,
  )

  // Pick the longest series so a missing ROI doesn't truncate the X axis.
  // In practice all 8 series should be the same length; this is defensive
  // against partial responses (e.g. a failed extraction for one ROI).
  const length = Math.max(0, ...Object.values(seriesByKey).map((s) => s.length))

  const frames: Frame[] = []
  for (let i = 0; i < length; i++) {
    const values: ROIValues = {} as ROIValues
    for (const k of ROI_KEYS) {
      values[k] = seriesByKey[k][i] ?? 0
    }
    frames.push({ t_s: parseFloat((i * tr).toFixed(3)), values })
  }
  return frames
}

// Shape ResultsView + its child components actually consume. Keeps the
// view code small and oblivious to the wire schema.
export type ResultsViewData = {
  frames: Frame[]
  insights: Insight[]
  duration_s: number
  assessment: OverallAssessment
  raw: AnalyzeResponse
}

export function adaptForResultsView(resp: AnalyzeResponse): ResultsViewData {
  return {
    frames: framesFromTimeline(resp.timeline),
    insights: resp.insights,
    duration_s: resp.duration_s,
    assessment: resp.overall_assessment,
    raw: resp,
  }
}
