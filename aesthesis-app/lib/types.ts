// Wire types — 1:1 mirror of aesthesis_app/aesthesis/schemas.py.
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
  // Shape (n_TRs, 400). Drives BrainCortical.tsx. Null when the TRIBE
  // worker hasn't been re-baked with the parcel map — frontend falls
  // back to the placeholder geometry. See ASSUMPTIONS_BRAIN.md §1.3.
  parcel_series: number[][] | null
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

// ── Phase 2 capture wire types ─────────────────────────────────────────────
// Mirror aesthesis_app/aesthesis/capture/protocol.py exactly. Source of
// truth is the Pydantic side. If you change a field on either side without
// changing the other, the request will fail loudly the first time it hits
// the network.

export type CookieSpec = {
  name: string
  value: string
  domain: string
  path?: string
  expires?: number | null
  httpOnly?: boolean | null
  secure?: boolean | null
  sameSite?: "Strict" | "Lax" | "None" | null
}

export type AuthSpec = {
  cookies?: CookieSpec[] | null
}

export type RunRequest = {
  url: string
  goal?: string | null
  auth?: AuthSpec | null
}

export type RunStartedResponse = {
  run_id: string
  status: "started"
}

export type CachedDemoEntry = {
  url: string
  label: string
  mp4_filename: string
}

// WS control-message union. Binary WS frames carry raw JPEG bytes and have
// NO envelope — anything received as a string is a control message matching
// one of these shapes (D30c: split JSON control / binary data).
//
// The legacy `frame` variant is kept for back-compat with existing test
// fixtures or older stubs but the v1.1+ pipeline never sends it (frames
// are always binary now).
export type WSMessage =
  | { type: "frame"; frame_b64: string }   // legacy — no longer sent
  | { type: "stream_degraded" }
  | {
      // Pre-warm complete: subprocess has launched Chromium, started CDP
      // screencast (frames are already flowing on the binary channel),
      // and constructed the LLM client. Frontend can enable the Start
      // button. The wall-clock D1 timer hasn't started yet — that fires
      // when /api/run/{id}/start is called.
      type: "prewarm_ready"
      run_id: string
      cdp_port: number
    }
  | {
      type: "capture_complete"
      run_id: string
      duration_s: number
      mp4_size_bytes: number
      n_actions: number
    }
  | {
      type: "capture_failed"
      run_id: string
      reason: "timeout" | "crashed" | "navigation_error" | "setup_error"
      message: string
    }
  | { type: "agent_event"; description: string; timestamp_ms: number }

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
