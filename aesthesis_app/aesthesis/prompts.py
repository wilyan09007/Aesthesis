"""Prompts for the Insight Synthesizer (DESIGN.md §4.5, post-pivot §17,
agent-prompt restructure ASSUMPTIONS_AGENT_PROMPT.md).

Two prompts:
- ``INSIGHT_PROMPT_TEMPLATE``: per-demo. Gemini sees the event records +
  per-event screenshots + the user's stated goal; emits one structured
  insight per event including a vision-grounded ``target_element``,
  ``proposed_change``, ``acceptance_criteria``, and a calibrated
  ``confidence`` score.
- ``ASSESSMENT_PROMPT_TEMPLATE``: holistic. Gemini sees the per-event
  insights + the absolute aggregate metrics; emits an ``OverallAssessment``
  JSON (summary + top_strengths + top_concerns + decisive_moment).

Both are formatted with ``.format(...)`` and the response is required to be
valid JSON. The synthesizer code handles repair / re-prompting if the
output is malformed.

The structured insight schema is the input to the deterministic Markdown
prompt renderer in ``prompt_renderer.py``. The renderer composes the
final paste-into-coding-agent string from these fields without an extra
LLM call — same input → same prompt, version-controllable.

Pre-pivot history: there used to be a ``VERDICT_PROMPT_TEMPLATE`` that
compared Version A and Version B and declared a winner. That whole frame
disappeared with the single-video pivot — DESIGN.md §17.
"""

INSIGHT_PROMPT_TEMPLATE = """\
You are analyzing how a first-time visitor's brain responded to a website
demo. The user is specifically evaluating: "{goal}".

You will see {n_events} brain events from a 30-second screen recording. For
each event you will see a screenshot of the exact frame at the event
timestamp plus the brain features that fired. Your job is to identify
the single UI element on screen most likely to have caused the brain
response and propose a concrete, implementable fix.

Brain feature → "look for" reference (use this to constrain element pick):
- friction_anxiety: dense, confusing, error-stating, or contradictory copy/controls
- cognitive_load: high-information regions (dense tables, walls of text, complex forms)
- aesthetic_appeal (low): visually broken layouts, clashing typography, unbalanced composition
- trust_affinity (low): sketchy patterns, missing trust signals
- reward_anticipation (high): CTAs, value props, demo-starts, pricing reveals
- motor_readiness (high): buttons / clickable elements the user is about to act on
- surprise_novelty: modals, layout shifts, unexpected animations
- visual_fluency (low): low-contrast text, weird fonts, broken hierarchy

Goal-as-prior: when choosing the target element, prefer elements
directly related to "{goal}" over peripheral ones, UNLESS the brain
signal clearly points elsewhere.

Per-event output schema (one entry per event in the same order):
{{
  "insights": [
    {{
      "timestamp_range_s": [start_s, end_s],
      "ux_observation": "one sentence — what on screen likely caused the brain response",
      "recommendation": "one sentence — short fix summary (legacy field; the structured proposed_change below is the source of truth)",
      "cited_brain_features": ["..."],
      "cited_screen_moment": "one phrase describing the visible UI moment",
      "target_element": {{
        "label": "short human label, e.g. 'Primary CTA — Start free trial'",
        "element_type": "button | heading | image | modal | input | link | table | icon | section | other",
        "visible_text": "exact on-screen characters of the element, or null if no readable text",
        "location_hint": "semantic location, e.g. 'upper-right of hero section', '3rd row of pricing table'",
        "visual_anchors": ["1-3 surrounding cues, e.g. 'under \\"Pricing\\" heading', 'right of trial CTA'"],
        "bbox_norm": [x0, y0, x1, y1]
      }},
      "proposed_change": {{
        "change_type": "copy | layout | hierarchy | color | spacing | typography | interaction | removal | addition | structure",
        "current_state": "describe the element's current visual/behavioral state",
        "desired_state": "describe the target state in concrete terms — copy text, sizes, weights, layout direction, etc.",
        "rationale": "tie the change to the cited brain features in 1 sentence"
      }},
      "acceptance_criteria": [
        "2-4 falsifiable bullets — each one testable by inspecting the final UI without re-running the brain analysis",
        "Bad: 'feels trustworthy'. Good: 'the primary CTA has higher contrast than every secondary action in the same viewport'."
      ],
      "confidence": 0.0
    }}
  ]
}}

Positive vs negative moments — IMPORTANT:

Not every notable brain event is a problem to fix. Some moments are
positive — things working well that the user should preserve. Examples:
  - reward_anticipation rising on a CTA the user is about to click
  - sustained motor_readiness during a checkout flow
  - aesthetic_appeal + visual_fluency rising together on a hero
  - flow_state windows
  - trust_affinity rising on social proof

For positive moments:
  - Set ``proposed_change`` to null
  - Set ``acceptance_criteria`` to []
  - STILL identify ``target_element`` so the user knows WHAT worked
  - Write the ``ux_observation`` in positive language ("the hero
    composition pulls reward_anticipation cleanly")
  - The frontend surfaces these as "working well — no change suggested";
    no agent prompt is generated for them.

For negative or actionable moments (friction spikes, cognitive load
spikes, bounce_risk windows, troughs, surprise that reads as confusion,
trust drops):
  - Fill ``proposed_change`` and ``acceptance_criteria`` as specified.

If the moment is genuinely neutral (a dominant_shift to nowhere
notable), prefer to omit the insight rather than invent a fix.

Hard constraints:

1. ONE insight per event. Do not aggregate or summarize across events.

2. Element grounding is MANDATORY. For every event, identify the single
   most likely UI element on screen. If the screenshot is too ambiguous
   to commit to one element, set ``target_element.label`` to a phrase
   beginning with "unclear: " and place 2 candidate descriptions in
   ``visual_anchors``. Do NOT skip the field.

3. ``visible_text`` MUST be the exact characters on the element if any
   are visible — do not paraphrase. If the element is a glyph / icon /
   image / chart with no readable copy, set ``visible_text`` to null.

4. ``bbox_norm`` must be ``[x0, y0, x1, y1]`` floats in [0, 1] of the
   screenshot frame. ``[0, 0]`` is top-left. Tighten the box around the
   element — do NOT wrap an entire section if a single button is the
   actual target. If you cannot localise, set ``bbox_norm`` to null.

5. ``acceptance_criteria`` MUST contain 2 to 4 entries. Each must be
   falsifiable — testable by inspecting the final UI. No vague entries
   like "feels intuitive".

6. ``confidence`` calibration:
   - 0.9-1.0: visible_text is clear + element type unambiguous + bbox tight
   - 0.7-0.9: clear winner among 2 candidates
   - 0.4-0.7: plausible but ambiguous; multiple elements could explain the signal
   - 0.0-0.4: no clear signal-element match; do not commit (use the unclear label)

7. ``change_type`` MUST be one of the listed enum values. If the right
   word doesn't fit, use "structure" (catch-all).

8. Do NOT use product-marketing language anywhere. No "delightful,"
   "intuitive," "seamless," "frictionless," "leverage," "robust."

9. Do NOT speculate beyond what the brain features support. If a signal
   fired but the screenshot is uninformative, mark the target as
   "unclear: ..." with confidence < 0.4 — the unclear branch is the
   honest answer, not a failure.

10. Output valid JSON conforming exactly to the schema above. No prose
    outside the JSON.

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
