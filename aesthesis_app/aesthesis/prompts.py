"""Prompts for the Insight Synthesizer (DESIGN.md §4.5 — verbatim).

Two prompts:
- INSIGHT_PROMPT_TEMPLATE: per-version. Gemini sees the event records +
  per-event screenshots + the user's stated goal; emits 1 insight per event.
- VERDICT_PROMPT_TEMPLATE: cross-version. Gemini sees the per-version
  insights + aggregate metrics; emits a `Verdict` JSON.

Both are formatted with `.format(...)` and the response is required to be
valid JSON. The synthesizer code handles repair / re-prompting if the
output is malformed.
"""

INSIGHT_PROMPT_TEMPLATE = """\
You are analyzing how a first-time visitor's brain responded to a website
demo. The user's stated goal was: "{goal}".

You will see {n_events} brain events from a 30-second screen recording. Each event
includes the brain signal that fired, the screenshot at that moment, and
what the user (an AI agent acting as a real user) did just before.

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
  implement it without asking questions. "Improve UX" is not a recommendation;
  "move the trial CTA above the fold and increase its weight to match the
  paid tiers" is.
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
      "version": "{version}",
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


VERDICT_PROMPT_TEMPLATE = """\
You have insights for Version A and Version B of the same product. The
user's goal was: "{goal}".

Aggregate metrics:
{metrics_table_json}

Per-version insights:
Version A:
{insights_a_json}

Version B:
{insights_b_json}

Produce a verdict in this schema:
{{
  "winner": "A" | "B" | "tie",
  "summary_paragraph": "...",
  "version_a_strengths": ["..."],
  "version_b_strengths": ["..."],
  "decisive_moment": "Version X at t=Ys: ..."
}}

Hard constraints:
- 3–5 sentences in summary_paragraph.
- Cite at least 2 specific timestamped moments in the summary.
- Cite at least 1 aggregate metric in the summary (e.g., "65% fewer
  friction spikes" or "flow_state window count 0 vs 2").
- If the result is genuinely ambiguous, return "tie" with reasoning;
  do not force a winner.
- Output valid JSON only. No prose outside.
"""
