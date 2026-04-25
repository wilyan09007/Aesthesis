import type { AnalyzeResponse, Frame, ROIValues } from "./types"

function wave(t: number, freq: number, phase: number, amp: number, base: number): number {
  const v = base + amp * Math.sin(freq * t + phase) + amp * 0.35 * Math.sin(2.3 * freq * t + phase * 1.7)
  return Math.max(0, Math.min(1, v))
}

function generateFrames(
  params: Record<keyof ROIValues, { freq: number; phase: number; amp: number; base: number }>,
  count: number,
  interval: number
): Frame[] {
  return Array.from({ length: count }, (_, i) => {
    const t = i * interval
    return {
      t_s: parseFloat(t.toFixed(1)),
      values: {
        aesthetic_appeal: wave(t, params.aesthetic_appeal.freq, params.aesthetic_appeal.phase, params.aesthetic_appeal.amp, params.aesthetic_appeal.base),
        visual_fluency: wave(t, params.visual_fluency.freq, params.visual_fluency.phase, params.visual_fluency.amp, params.visual_fluency.base),
        cognitive_load: wave(t, params.cognitive_load.freq, params.cognitive_load.phase, params.cognitive_load.amp, params.cognitive_load.base),
        trust_affinity: wave(t, params.trust_affinity.freq, params.trust_affinity.phase, params.trust_affinity.amp, params.trust_affinity.base),
        reward_anticipation: wave(t, params.reward_anticipation.freq, params.reward_anticipation.phase, params.reward_anticipation.amp, params.reward_anticipation.base),
        motor_readiness: wave(t, params.motor_readiness.freq, params.motor_readiness.phase, params.motor_readiness.amp, params.motor_readiness.base),
        surprise_novelty: wave(t, params.surprise_novelty.freq, params.surprise_novelty.phase, params.surprise_novelty.amp, params.surprise_novelty.base),
        friction_anxiety: wave(t, params.friction_anxiety.freq, params.friction_anxiety.phase, params.friction_anxiety.amp, params.friction_anxiety.base),
      },
    }
  })
}

const paramsA: Record<keyof ROIValues, { freq: number; phase: number; amp: number; base: number }> = {
  aesthetic_appeal:    { freq: 0.28, phase: 0.0,  amp: 0.11, base: 0.78 },
  visual_fluency:      { freq: 0.22, phase: 0.8,  amp: 0.10, base: 0.73 },
  cognitive_load:      { freq: 0.35, phase: 1.2,  amp: 0.09, base: 0.27 },
  trust_affinity:      { freq: 0.18, phase: 2.1,  amp: 0.12, base: 0.71 },
  reward_anticipation: { freq: 0.20, phase: 0.4,  amp: 0.13, base: 0.74 },
  motor_readiness:     { freq: 0.31, phase: 1.6,  amp: 0.10, base: 0.54 },
  surprise_novelty:    { freq: 0.40, phase: 0.9,  amp: 0.11, base: 0.43 },
  friction_anxiety:    { freq: 0.45, phase: 2.4,  amp: 0.08, base: 0.18 },
}

const paramsB: Record<keyof ROIValues, { freq: number; phase: number; amp: number; base: number }> = {
  aesthetic_appeal:    { freq: 0.29, phase: 1.1,  amp: 0.13, base: 0.51 },
  visual_fluency:      { freq: 0.38, phase: 0.3,  amp: 0.14, base: 0.48 },
  cognitive_load:      { freq: 0.26, phase: 1.8,  amp: 0.17, base: 0.58 },
  trust_affinity:      { freq: 0.21, phase: 2.7,  amp: 0.13, base: 0.45 },
  reward_anticipation: { freq: 0.23, phase: 1.3,  amp: 0.14, base: 0.49 },
  motor_readiness:     { freq: 0.33, phase: 0.6,  amp: 0.11, base: 0.52 },
  surprise_novelty:    { freq: 0.37, phase: 1.5,  amp: 0.16, base: 0.61 },
  friction_anxiety:    { freq: 0.42, phase: 0.2,  amp: 0.15, base: 0.47 },
}

export const MOCK_DATA: AnalyzeResponse = {
  a: {
    frames: generateFrames(paramsA, 36, 0.5),
    insights: [
      {
        timestamp_range_s: [0.5, 3.0],
        ux_observation: "Aesthetic appeal and visual fluency spike immediately on load. The brain registers a high-quality, well-structured visual hierarchy within the first 500ms.",
        recommendation: "Preserve the hero section layout and above-fold composition — the neural response here is a strong first impression driver.",
      },
      {
        timestamp_range_s: [4.0, 7.5],
        ux_observation: "Trust affinity climbs steadily as the user encounters social proof and clear value propositions. Reward anticipation co-activates, signaling building desire.",
        recommendation: "Keep the social proof elements visible during scroll. Consider surfacing them even earlier on mobile to lock in trust before the CTA.",
      },
      {
        timestamp_range_s: [9.0, 12.5],
        ux_observation: "Motor readiness peaks as the primary CTA enters the viewport — the brain is priming action. Friction anxiety stays suppressed, indicating a frictionless path.",
        recommendation: "This is the ideal moment for the CTA. Ensure nothing interrupts the user's gaze path between the value prop and the button.",
      },
      {
        timestamp_range_s: [13.5, 17.5],
        ux_observation: "Reward anticipation reaches its session maximum near the end, suggesting the closing section successfully reinforces desire. Aesthetic appeal remains elevated.",
        recommendation: "The closing section is unusually effective — replicate its pattern (large imagery, sparse copy, confident tone) earlier in the funnel.",
      },
    ],
  },
  b: {
    frames: generateFrames(paramsB, 36, 0.5),
    insights: [
      {
        timestamp_range_s: [0.0, 2.5],
        ux_observation: "Cognitive load spikes sharply in the first 2 seconds. Multiple competing visual elements create parsing overhead before the brain can orient to a primary focus.",
        recommendation: "Reduce above-fold element density by at least 40%. Pick one dominant visual and one headline — eliminate supporting copy until scroll.",
      },
      {
        timestamp_range_s: [5.0, 9.0],
        ux_observation: "Friction anxiety elevates significantly through the mid-section, correlating with dense form fields and unclear interaction affordances. Surprise-novelty is elevated but in a disorienting way.",
        recommendation: "Audit the mid-section for decision points. Each required choice without clear guidance is a friction spike — consolidate or defer optional fields.",
      },
      {
        timestamp_range_s: [10.0, 13.0],
        ux_observation: "Trust affinity dips at the lowest point of the session exactly when the pricing section appears without adequate context. The brain reads ambiguity as risk.",
        recommendation: "Precede pricing with a brief ROI statement or social proof. The neural trust signal must be above baseline before the price is revealed.",
      },
      {
        timestamp_range_s: [14.0, 17.5],
        ux_observation: "Reward anticipation never recovers to match Version A levels. The closing section fails to amplify desire, leaving the session on a neutral rather than positive note.",
        recommendation: "Rewrite the closing section to lead with outcome imagery and a single bold statement about what the user gains. Remove footer-heavy link clusters from the closing view.",
      },
    ],
  },
  verdict: {
    winner: "A",
    summary:
      "Version A demonstrates meaningfully superior neural engagement across all high-signal ROIs. It generates 34% higher average reward anticipation, 58% lower friction anxiety, and a trust affinity trajectory that remains elevated through the closing CTA. Version B's primary liability is a cognitive load spike in the first 2 seconds that suppresses trust and never fully recovers. The delta is not marginal — A wins on brain response at every critical decision moment in the funnel.",
  },
}
