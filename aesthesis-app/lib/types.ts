export type ROIValues = {
  aesthetic_appeal: number
  visual_fluency: number
  cognitive_load: number
  trust_affinity: number
  reward_anticipation: number
  motor_readiness: number
  surprise_novelty: number
  friction_anxiety: number
}

export type Frame = {
  t_s: number
  values: ROIValues
}

export type Insight = {
  timestamp_range_s: [number, number]
  ux_observation: string
  recommendation: string
}

export type VersionResult = {
  frames: Frame[]
  insights: Insight[]
}

export type AnalyzeResponse = {
  a: VersionResult
  b: VersionResult
  verdict: {
    winner: "A" | "B" | "tie"
    summary: string
  }
}

export type WSMessage =
  | {
      type: "frame"
      version: "A" | "B"
      frame_b64: string
    }
  | {
      type: "stream_degraded"
      version: "A" | "B"
    }

export type AppState = "landing" | "capture" | "assess" | "analyzing" | "results"

export type CaptureInputs = {
  urlA: string
  urlB: string
  goal: string
}

export type VideoFiles = {
  a: File | null
  b: File | null
}

export const ROI_KEYS: (keyof ROIValues)[] = [
  "aesthetic_appeal",
  "visual_fluency",
  "cognitive_load",
  "trust_affinity",
  "reward_anticipation",
  "motor_readiness",
  "surprise_novelty",
  "friction_anxiety",
]

export const ROI_LABELS: Record<keyof ROIValues, string> = {
  aesthetic_appeal: "Aesthetic Appeal",
  visual_fluency: "Visual Fluency",
  cognitive_load: "Cognitive Load",
  trust_affinity: "Trust Affinity",
  reward_anticipation: "Reward Anticipation",
  motor_readiness: "Motor Readiness",
  surprise_novelty: "Surprise / Novelty",
  friction_anxiety: "Friction / Anxiety",
}

export const ROI_COLORS: Record<keyof ROIValues, string> = {
  aesthetic_appeal: "#A78BFA",
  visual_fluency: "#38BDF8",
  cognitive_load: "#7C9CFF",
  trust_affinity: "#34D399",
  reward_anticipation: "#5CF2C5",
  motor_readiness: "#FBBF24",
  surprise_novelty: "#F472B6",
  friction_anxiety: "#FF6B6B",
}