"""Deterministic Markdown renderer for the agent-paste prompt.

Same structured input → same prompt. No second LLM call. The template
lives here in source so we can lint it, version it, and roll it forward
without prompt regression risk.

Contract (ASSUMPTIONS_AGENT_PROMPT.md §4):
    Input: an ``Insight`` populated with ``target_element`` +
    ``proposed_change`` + ``acceptance_criteria`` + ``confidence``,
    plus the run's optional ``goal``.
    Output: a Markdown string the user copies to clipboard and pastes
    into Claude Code / Cursor / Aider / Copilot Chat / etc.

The template has three branches:
    standard   confidence >= 0.7  — full agent-paste prompt
    cautious   0.4 <= conf < 0.7  — same prompt + "verify before applying" line
    unclear    confidence < 0.4 OR ``label`` starts with "unclear:"
                                  — describes 2 candidates, asks the agent to
                                    investigate rather than commit a fix

Every branch ends with explicit "search by visible text first, then narrow
by anchors" guidance — reflects what's known to work across coding agents
(see §12 of the assumptions doc).
"""

from __future__ import annotations

import logging
from typing import Iterable

from .schemas import Insight, ProposedChange, TargetElement

log = logging.getLogger(__name__)


#: Brain feature → short natural-language phrase. Used to render the
#: "the viewer's brain showed X" sentence in the prompt context.
ROI_NATURAL_LANGUAGE: dict[str, str] = {
    "friction_anxiety": "elevated friction",
    "cognitive_load": "increased cognitive effort",
    "aesthetic_appeal": "a drop in aesthetic appeal",
    "trust_affinity": "a reduced trust signal",
    "reward_anticipation": "lowered reward anticipation",
    "motor_readiness": "a click-readiness drop",
    "surprise_novelty": "a surprise spike",
    "visual_fluency": "visual processing strain",
}


def _phrase_brain_features(features: Iterable[str]) -> str:
    """Compose a short readable phrase from cited brain feature keys."""
    parts: list[str] = []
    for f in features:
        phrase = ROI_NATURAL_LANGUAGE.get(f)
        parts.append(phrase if phrase else f.replace("_", " "))
    if not parts:
        return "an unexpected response"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} paired with {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _short_summary(text: str) -> str:
    """First sentence of an observation, with a hard 240-char cap."""
    text = (text or "").strip()
    if not text:
        return ""
    for sep in (". ", ".\n", "! ", "? "):
        idx = text.find(sep)
        if idx > 0:
            text = text[: idx + 1]
            break
    return text[:240]


def _quoted_or_fallback(s: str | None) -> str:
    if not s:
        return ("(no visible text — element is identified by location and "
                "anchors only)")
    # Escape any internal quotes for the Markdown rendering.
    return f"\"{s}\""


def _bulletise(items: Iterable[str], *, prefix: str = "- ") -> str:
    out: list[str] = []
    for it in items:
        line = (it or "").strip()
        if not line:
            continue
        out.append(f"{prefix}{line}")
    return "\n".join(out) if out else f"{prefix}(none provided)"


def _is_unclear_target(target: TargetElement | None) -> bool:
    if target is None:
        return True
    label = (target.label or "").strip().lower()
    return label.startswith("unclear")


def _confidence_band(confidence: float, target: TargetElement | None) -> str:
    """One of: ``standard`` | ``cautious`` | ``unclear``."""
    if _is_unclear_target(target):
        return "unclear"
    if confidence is None:
        return "standard"
    if confidence < 0.4:
        return "unclear"
    if confidence < 0.7:
        return "cautious"
    return "standard"


# ─── Branch templates ───────────────────────────────────────────────────────


def _render_standard(
    *,
    insight: Insight,
    target: TargetElement,
    change: ProposedChange,
    goal: str | None,
    cautious: bool,
) -> str:
    t0, t1 = insight.timestamp_range_s
    brain_phrase = _phrase_brain_features(insight.cited_brain_features)
    obs_short = _short_summary(insight.ux_observation)

    visible_text_quoted = _quoted_or_fallback(target.visible_text)
    anchors_block = _bulletise(target.visual_anchors or [], prefix="  - ")
    criteria_block = _bulletise(insight.acceptance_criteria or [],
                                prefix="- [ ] ")
    cited_csv = ", ".join(insight.cited_brain_features) or "(none)"
    goal_line = (
        f"The user is specifically evaluating: \"{goal}\".\n\n"
        if goal else ""
    )

    cautious_note = (
        "\n> ⚠ Medium-confidence pick — verify the element matches before "
        "applying the change. If the description doesn't match anything in "
        "the codebase, fall back to the acceptance criteria as the contract.\n"
        if cautious else ""
    )

    visible_text_search = (
        f"the string {visible_text_quoted}"
        if target.visible_text
        else "elements matching the visual description above"
    )

    return f"""\
# UX fix from neural-response analysis

You are a coding agent. Implement this UI change in the user's codebase.
The change is grounded in a brain-response analysis of a screen recording
of the user's product — at the timestamp below, the viewer's brain showed
{brain_phrase}, indicating {obs_short or insight.ux_observation}.
{cautious_note}
{goal_line}## What to change

**Element:** {target.label}
- **Type:** {target.element_type or 'element'}
- **Visible text:** {visible_text_quoted}
- **Location:** {target.location_hint or '(not specified)'}
- **Visual anchors (use these to find the element in source):**
{anchors_block}

**Reference screenshot:** see the attached image (the highlighted box
marks the target element).

**Change ({change.change_type}):**
- **Current state:** {change.current_state}
- **Desired state:** {change.desired_state}
- **Why this is the right move:** {change.rationale}

## Acceptance criteria

The change is correct when all of the following hold:

{criteria_block}

## How to find the element in the codebase

1. Search for {visible_text_search}. If multiple matches, narrow by the
   location hint and visual anchors.
2. Confirm the element is a `{target.element_type or 'element'}` whose
   surroundings match the visual anchors.
3. If the framework template is dynamic (loops, slots), trace upward to
   the component that renders this region rather than editing the raw
   output.
4. Apply the change. Preserve every existing prop / class / handler that
   isn't part of the diff.

## Brain context (for your understanding, not a constraint)

- Time window: {t0:.1f}s – {t1:.1f}s of the demo recording
- Cited brain features: {cited_csv}
- Screen moment: "{insight.cited_screen_moment}"

If, after locating the element, you believe a different fix would
better satisfy the acceptance criteria, prefer the criteria over the
literal `desired_state` text — that text is one valid implementation,
not the only one.
"""


def _render_unclear(
    *,
    insight: Insight,
    target: TargetElement | None,
    change: ProposedChange | None,
    goal: str | None,
) -> str:
    t0, t1 = insight.timestamp_range_s
    brain_phrase = _phrase_brain_features(insight.cited_brain_features)

    candidates = (target.visual_anchors if target else []) or [
        "no candidate descriptors were generated"
    ]
    candidates_block = _bulletise(candidates, prefix="  - ")
    criteria_block = (
        _bulletise(insight.acceptance_criteria, prefix="- [ ] ")
        if insight.acceptance_criteria
        else "- [ ] (no criteria — the brain signal didn't yield a "
             "falsifiable check)"
    )
    cited_csv = ", ".join(insight.cited_brain_features) or "(none)"
    goal_line = (
        f"The user is specifically evaluating: \"{goal}\".\n\n"
        if goal else ""
    )

    change_block = ""
    if change is not None:
        change_block = (
            f"\n## Direction (low confidence — verify before acting)\n\n"
            f"- **Type of change:** {change.change_type}\n"
            f"- **Suggested direction:** {change.desired_state}\n"
            f"- **Why:** {change.rationale}\n"
        )

    return f"""\
# UX investigation from neural-response analysis (low confidence)

You are a coding agent. The brain analysis flagged a moment, but
identifying the exact UI element from a screenshot was inconclusive.
**Investigate before changing anything.** Below are the brain context,
candidate elements, and acceptance criteria.

At t={t0:.1f}–{t1:.1f}s the viewer's brain showed {brain_phrase}, and the
analysis observed: {insight.ux_observation}

{goal_line}## Candidate elements (verify which is actually the cause)

{candidates_block}

## Acceptance criteria for any fix you eventually apply

{criteria_block}
{change_block}
## Recommended workflow

1. Open the user's product to the screen at roughly t={t0:.1f}s.
2. For each candidate above, locate it in the codebase (search by
   visible text or by location).
3. Pick the one whose context best matches "{insight.cited_screen_moment}"
   and the cited brain features ({cited_csv}).
4. If unsure, ask the user to confirm before editing.
5. Apply a change consistent with the acceptance criteria.

This prompt is intentionally non-prescriptive because the original
analysis confidence was below the commit threshold.
"""


# ─── Public entry point ─────────────────────────────────────────────────────


def render_agent_prompt(insight: Insight, *, goal: str | None) -> str:
    """Render the paste-into-coding-agent Markdown prompt.

    Pulls the structured fields off ``insight`` and routes through one of
    three branch templates. The unclear branch is used when confidence
    is below 0.4 or the label is prefixed with "unclear:". This is the
    public contract — the synthesizer calls this once per insight after
    the Gemini insight call returns.
    """
    band = _confidence_band(insight.confidence, insight.target_element)
    log.debug(
        "rendering agent prompt",
        extra={"step": "prompt_renderer", "band": band,
               "confidence": insight.confidence,
               "label": (insight.target_element.label
                         if insight.target_element else None)},
    )

    if band == "unclear":
        return _render_unclear(
            insight=insight,
            target=insight.target_element,
            change=insight.proposed_change,
            goal=goal,
        )

    target = insight.target_element
    change = insight.proposed_change
    if target is None or change is None:
        # Defensive: structured fields somehow missing despite a healthy
        # confidence score. Degrade to unclear branch rather than crash.
        log.info(
            "structured fields missing on a non-unclear insight — "
            "falling back to unclear branch",
            extra={"step": "prompt_renderer",
                   "confidence": insight.confidence},
        )
        return _render_unclear(
            insight=insight, target=target, change=change, goal=goal,
        )

    return _render_standard(
        insight=insight,
        target=target,
        change=change,
        goal=goal,
        cautious=(band == "cautious"),
    )
