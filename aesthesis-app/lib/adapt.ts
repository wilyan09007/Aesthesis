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
  // (n_TRs, 400) per-parcel z-scored activations. Null when the TRIBE
  // worker hasn't been baked with the parcel map. BrainCortical falls
  // back to the placeholder when this is null. ASSUMPTIONS_BRAIN.md §3.6.
  parcel_series: number[][] | null
  // Lifted from raw.timeline so BrainCortical doesn't need to dig.
  tr_duration_s: number
  raw: AnalyzeResponse
}

export function adaptForResultsView(resp: AnalyzeResponse): ResultsViewData {
  // Verbose dev console — ASSUMPTIONS_BRAIN.md §5.3. Lets you tell at a
  // glance whether the cortical brain will render or fall back. Console
  // log lines are cheap; stripped by Next.js minifier in prod builds.
  const parcelSeries = resp.timeline.parcel_series ?? null
  // eslint-disable-next-line no-console
  console.info("[adapt] parcel_series",
    parcelSeries
      ? { n_trs: parcelSeries.length, n_parcels: parcelSeries[0]?.length ?? 0 }
      : "null (cortical brain will fall back to placeholder)",
  )
  return {
    frames: framesFromTimeline(resp.timeline),
    insights: resp.insights,
    duration_s: resp.duration_s,
    assessment: resp.overall_assessment,
    parcel_series: parcelSeries,
    tr_duration_s: resp.timeline.tr_duration_s,
    raw: resp,
  }
}
