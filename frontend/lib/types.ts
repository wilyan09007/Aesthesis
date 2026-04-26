// Wire types — 1:1 mirror of backend/aesthesis/schemas.py.
// The Pydantic schema is the single source of truth. If you change a field
// here without changing it there (or vice versa), the request will fail
// loudly the first time it hits the network.
//
// Single-video pivot (DESIGN.md §17): no A/B split. One video in, one
// AnalyzeResponse out. The legacy VersionTag / Verdict / VersionResult
// types are gone.

export type EventType =
  | "spike"
  | "dominant_shift"
  | "sustained"
  | "co_movement"
  | "trough"
  | "flow"
  | "bounce_risk"

// The 8 ROI keys come from NETWORK_KEYS_UX in tribe_neural — DESIGN.md §5.15.
// Keep this list in lockstep with the backend; it drives chart colors,
// labels, the 3D brain, and adapter logic.
export type ROIKey =
  | "aesthetic_appeal"
  | "visual_fluency"
  | "cognitive_load"
  | "trust_affinity"
  | "reward_anticipation"
  | "motor_readiness"
  | "surprise_novelty"
  | "friction_anxiety"

// Per-frame ROI snapshot — derived from TimelineSummary.roi_series + tr_duration_s.
// Backend doesn't ship this shape directly; lib/adapt.ts produces it for
// the chart/3D-brain components that already consume it.
export type ROIValues = Record<ROIKey, number>

export type Frame = {
  t_s: number
  values: ROIValues
}

// ── Wire types (mirror schemas.py exactly) ─────────────────────────────────

export type Insight = {
  timestamp_range_s: [number, number]
  ux_observation: string
  recommendation: string
  cited_brain_features: string[]
  cited_screen_moment: string
}

export type Event = {
  timestamp_s: number
  type: EventType
  primary_roi: string | null
  magnitude: number
  co_events: string[]
  agent_action_at_t: string | null
  screenshot_path: string | null
  screenshot_b64: string | null
}

export type AggregateMetric = {
  name: string
  value: number
  interpretation: string | null
}

export type OverallAssessment = {
  summary_paragraph: string
  top_strengths: string[]
  top_concerns: string[]
  decisive_moment: string
}

export type TimelineSummary = {
  n_trs: number
  tr_duration_s: number
  roi_series: Record<string, number[]>
  composites_series: Record<string, number[]>
  windows: Array<Record<string, unknown>>
  processing_time_ms: number
  // Per-parcel z-scored activations (Schaefer-400 atlas, fsaverage5).
  // Shape (n_TRs, 400). Kept as a fallback / debugging signal; the
  // primary brain renderer uses face_colors below. See
  // ASSUMPTIONS_BRAIN.md §1.3.
  parcel_series: number[][] | null
  // Per-face uint8 RGB stream — matches Meta's TRIBE v2 demo format
  // exactly. shape per hemi = (n_TRs, 20480, 3). data_b64 is base64 of
  // the C-contiguous binary. When present, BrainCortical renders via
  // the per-face shader pattern Meta uses (sharp boundaries, GPU-only
  // sampling, zero CPU per frame).
  face_colors: {
    left: HemisphereFaceColors
    right: HemisphereFaceColors
  } | null
}

export type HemisphereFaceColors = {
  // "uint8_rgba_bin" is the current glass-brain format (4 channels —
  // RGB + activation-driven alpha). "uint8_rgb_bin" is the legacy
  // 3-channel format from earlier deploys; the frontend
  // (buildAtlasTexture in BrainCortical.tsx) auto-detects from the
  // byte count and treats legacy streams as fully opaque.
  format: "uint8_rgba_bin" | "uint8_rgb_bin"
  shape: [number, number, number] // [n_frames, n_faces, 3 | 4]
  n_frames: number
  n_faces: number
  data_b64: string
}

export type AnalyzeRequestMeta = {
  goal: string | null
  run_id: string
  received_at: string
}

export type AnalyzeResponse = {
  meta: AnalyzeRequestMeta
  video_url: string | null
  duration_s: number
  timeline: TimelineSummary
  events: Event[]
  insights: Insight[]
  aggregate_metrics: AggregateMetric[]
  overall_assessment: OverallAssessment
  elapsed_ms: number
}

export type ValidationFailure = {
  field: string
  error: string
  details?: Record<string, unknown> | null
}

// ── Local UI types ─────────────────────────────────────────────────────────

export type AppState = "landing" | "capture" | "assess" | "analyzing" | "results"

export type CaptureInputs = {
  url: string
  goal: string
}

export type WSMessage =
  | { type: "frame"; frame_b64: string }
  | { type: "stream_degraded" }

// ── ROI display constants ──────────────────────────────────────────────────

export const ROI_KEYS: ROIKey[] = [
  "aesthetic_appeal",
  "visual_fluency",
  "cognitive_load",
  "trust_affinity",
  "reward_anticipation",
  "motor_readiness",
  "surprise_novelty",
  "friction_anxiety",
]

export const ROI_LABELS: Record<ROIKey, string> = {
  aesthetic_appeal: "Aesthetic Appeal",
  visual_fluency: "Visual Fluency",
  cognitive_load: "Cognitive Load",
  trust_affinity: "Trust Affinity",
  reward_anticipation: "Reward Anticipation",
  motor_readiness: "Motor Readiness",
  surprise_novelty: "Surprise / Novelty",
  friction_anxiety: "Friction / Anxiety",
}

export const ROI_COLORS: Record<ROIKey, string> = {
  aesthetic_appeal: "#A78BFA",
  visual_fluency: "#38BDF8",
  cognitive_load: "#7C9CFF",
  trust_affinity: "#34D399",
  reward_anticipation: "#5CF2C5",
  motor_readiness: "#FBBF24",
  surprise_novelty: "#F472B6",
  friction_anxiety: "#FF6B6B",
}
