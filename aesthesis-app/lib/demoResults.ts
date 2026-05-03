// DEMO FIXTURE — for UI/UX work. Activated by visiting /?demo
// Delete this file (and the matching block in app/page.tsx) to revert.

import type { AnalyzeResponse, ROIKey } from "./types"
import { ROI_KEYS } from "./types"

const N_TRS = 20
const TR = 1.5

function series(seed: number): number[] {
  const out: number[] = []
  for (let i = 0; i < N_TRS; i++) {
    const phase = (i / N_TRS) * Math.PI * 2 + seed
    out.push(parseFloat((Math.sin(phase) * 0.7 + Math.cos(phase * 1.7) * 0.3).toFixed(4)))
  }
  return out
}

const roi_series: Record<string, number[]> = ROI_KEYS.reduce(
  (acc, k: ROIKey, i) => { acc[k] = series(i * 0.7); return acc },
  {} as Record<string, number[]>,
)

export const demoAnalyzeResponse: AnalyzeResponse = {
  meta: { goal: "demo run for UI work", run_id: "demo-run", received_at: new Date().toISOString() },
  video_url: null,
  duration_s: N_TRS * TR,
  timeline: {
    n_trs: N_TRS,
    tr_duration_s: TR,
    roi_series,
    composites_series: { appeal_index: series(2.1), flow_state: series(0.3) },
    windows: [],
    processing_time_ms: 4200,
    parcel_series: null,
    face_colors: null,
  },
  events: [],
  insights: [
    {
      timestamp_range_s: [2.0, 6.0],
      ux_observation: "Strong aesthetic appeal during hero section — visual fluency rises while cognitive load stays low.",
      recommendation: "Keep the hero composition; this is the demo's strongest moment.",
      cited_brain_features: ["aesthetic_appeal ↑", "visual_fluency ↑", "cognitive_load flat"],
      cited_screen_moment: "Landing hero, ~3s in",
      target_element: null,
      proposed_change: null,
      acceptance_criteria: [],
      confidence: 0,
      agent_prompt: "",
      annotated_screenshot_b64: null,
    },
    {
      timestamp_range_s: [12.0, 16.0],
      ux_observation: "Friction spike co-occurs with a drop in reward anticipation — likely confusion at the form step.",
      recommendation: "Simplify the form copy or surface progress earlier.",
      cited_brain_features: ["friction_anxiety spike", "reward_anticipation ↓"],
      cited_screen_moment: "Signup form transition, ~13s",
      target_element: null,
      proposed_change: null,
      acceptance_criteria: [],
      confidence: 0,
      agent_prompt: "",
      annotated_screenshot_b64: null,
    },
    {
      timestamp_range_s: [22.0, 28.0],
      ux_observation: "Sustained motor readiness with rising trust affinity — user is committed and clicking through.",
      recommendation: "Place the primary CTA here; engagement signals are converging.",
      cited_brain_features: ["motor_readiness sustained", "trust_affinity ↑"],
      cited_screen_moment: "Pricing section, ~25s",
      target_element: null,
      proposed_change: null,
      acceptance_criteria: [],
      confidence: 0,
      agent_prompt: "",
      annotated_screenshot_b64: null,
    },
  ],
  aggregate_metrics: [
    { name: "mean_appeal_index", value: 0.18, interpretation: "appeal arc skewed positive" },
    { name: "friction_spike_count", value: 1, interpretation: "1 friction spike(s) detected" },
    { name: "flow_state_windows", value: 2, interpretation: "2 flow-state window(s)" },
  ],
  overall_assessment: {
    summary_paragraph:
      "The demo opens strong with high aesthetic appeal and easy visual fluency, briefly dips at the form step where friction spikes, then recovers with sustained motor readiness through the pricing section. The arc is net-positive but the form moment is the obvious lever.",
    top_strengths: [
      "Hero section drives appeal without taxing cognitive load.",
      "Pricing section converts engagement into commitment signals.",
    ],
    top_concerns: [
      "Form step triggers a clear friction spike.",
      "Reward anticipation dips before recovering — the gap is felt.",
    ],
    decisive_moment: "The 13s friction spike at the form transition.",
  },
  elapsed_ms: 8400,
}
