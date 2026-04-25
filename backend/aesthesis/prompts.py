"""Prompts for the Insight Synthesizer (DESIGN.md §4.5, post-pivot §17).

Two prompts:
- ``INSIGHT_PROMPT_TEMPLATE``: per-demo. Gemini sees the event records +
  per-event screenshots + the user's stated goal; emits 1 insight per event.
- ``ASSESSMENT_PROMPT_TEMPLATE``: holistic. Gemini sees the per-event
  insights + the absolute aggregate metrics; emits an ``OverallAssessment``
  JSON (summary + top_strengths + top_concerns + decisive_moment).

Both are formatted with ``.format(...)`` and the response is required to be
valid JSON. The synthesizer code handles repair / re-prompting if the
output is malformed.

Pre-pivot history: there used to be a ``VERDICT_PROMPT_TEMPLATE`` that
compared Version A and Version B and declared a winner. That whole frame
disappeared with the single-video pivot — DESIGN.md §17.
"""

INSIGHT_PROMPT_TEMPLATE = """\
You are analyzing how a first-time visitor's brain responded to a website
demo. The user's stated goal was: "{goal}".

You will see {n_events} brain events from a 30-second screen recording. Each
event includes the brain signal that fired, the screenshot at that moment,
and what the user (an AI agent acting as a real user) did just before.

Your job: for each event, produce ONE insight in this format:
- ux_observation: one sentence explaining what on the screen likely caused
  this brain response. Be concrete — name the UI element, the copy, the
  layout choice.
- recommendation: one sentence with a specific, implementable change.
- cited_brain_features: list of the brain feature keys you used.
- cited_screen_moment: one phrase describing the visible UI moment.

Hard constraints:
- One insight per event. Do not aggregate or summarize across events.
- Each insight MUST cite at least one brain feature key.
- Each recommendation MUST be concrete enough that an engineer could
  implement it without asking questions. "Improve UX" is not a
  recommendation; "move the trial CTA above the fold and increase its
  weight to match the paid tiers" is.
- Do NOT use product-marketing language. No "delightful," "intuitive,"
  "seamless," "frictionless," "leverage," "robust."
- Do NOT speculate beyond what the brain features support. If fear spiked
  but you can't tell from the screenshot why, say "screenshot does not
  reveal cause; possible candidates: X, Y."
- Output valid JSON conforming exactly to the output schema. No prose
  outside the JSON.

Output schema:
{{
  "insights": [
    {{
      "timestamp_range_s": [start_s, end_s],
      "ux_observation": "...",
      "recommendation": "...",
      "cited_brain_features": ["..."],
      "cited_screen_moment": "..."
    }}
  ]
}}

Events JSON:
{events_json}
"""


ASSESSMENT_PROMPT_TEMPLATE = """\
You are summarising the brain's overall reaction to a single 30-second
website demo. The user's goal was: "{goal}".

You have:
1. ABSOLUTE METRICS — eight measurements scored against this demo's own
   timeline. ``mean_appeal_index`` near 0 is neutral; ``friction_spike_count``
   is the raw count of friction spikes; ``flow_state_windows`` is the count
   of sliding windows that hit flow-state criteria; etc.
2. PER-EVENT INSIGHTS — Gemini's reading of each notable brain event with
   timestamps, observations, and recommendations.

Aggregate metrics:
{metrics_table_json}

Per-event insights:
{insights_json}

Produce a single ``OverallAssessment`` JSON in this schema:
{{
  "summary_paragraph": "...",
  "top_strengths": ["..."],
  "top_concerns": ["..."],
  "decisive_moment": "t=Ys: ..."
}}

Hard constraints:
- ``summary_paragraph`` is 3-5 sentences narrating the brain arc across
  the demo (not the implementation, not the product). Cite at least 2
  specific timestamps and at least 1 metric ("3 friction spikes",
  "no flow-state windows", "motor_readiness peaked at t=14s", etc.).
- ``top_strengths`` is 1-3 bullets, each grounded in a positive brain
  moment with timestamp.
- ``top_concerns`` is 1-3 bullets, each grounded in a friction / load /
  bounce-risk moment with timestamp.
- ``decisive_moment`` is one sentence pointing at the single most
  consequential timestamp for this demo's first impression.
- Do NOT use product-marketing language. No "delightful," "intuitive,"
  "seamless," "frictionless," "leverage," "robust."
- Output valid JSON only. No prose outside.
"""
