"""Prompt-renderer tests — deterministic output across all three branches.

Same structured input MUST produce the same Markdown. If somebody
edits the template casually, these tests trip and force a deliberate
update of the assertions. That is the design — the prompt is the
final product, the template lives in source, and the tests are the
contract.
"""

from __future__ import annotations

import pytest

from aesthesis.prompt_renderer import (
    _confidence_band,
    _phrase_brain_features,
    render_agent_prompt,
)
from aesthesis.schemas import Insight, ProposedChange, TargetElement


def _standard_insight(**overrides) -> Insight:
    """Build a fully-populated, high-confidence insight."""
    target = TargetElement(
        label="Primary CTA — Start free trial",
        element_type="button",
        visible_text="Start free trial",
        location_hint="upper-right of hero section",
        visual_anchors=["under 'Pricing' heading", "right of feature list"],
        bbox_norm=(0.78, 0.05, 0.96, 0.12),
    )
    change = ProposedChange(
        change_type="hierarchy",
        current_state="Outline button at 12px label, lower contrast than secondary CTAs",
        desired_state="Filled primary background, 16px label, dominates the viewport",
        rationale="reward_anticipation rose but motor_readiness stayed flat — visitors recognise value but don't feel pulled to act",
    )
    base = dict(
        timestamp_range_s=(2.5, 4.0),
        ux_observation="The trial CTA is recognised as desirable but lacks visual weight to pull a click.",
        recommendation="Increase weight of the primary CTA",
        cited_brain_features=["reward_anticipation", "motor_readiness"],
        cited_screen_moment="hero with pricing teaser visible",
        target_element=target,
        proposed_change=change,
        acceptance_criteria=[
            "primary CTA has higher contrast than every secondary action in the same viewport",
            "primary CTA's label is at least 16px",
            "no other button on the screen draws the eye first",
        ],
        confidence=0.85,
        agent_prompt="",
        annotated_screenshot_b64=None,
    )
    base.update(overrides)
    return Insight(**base)


# ─── confidence band routing ────────────────────────────────────────────────


def test_band_standard_for_high_confidence():
    ins = _standard_insight(confidence=0.85)
    assert _confidence_band(ins.confidence, ins.target_element) == "standard"


def test_band_cautious_for_mid_confidence():
    ins = _standard_insight(confidence=0.55)
    assert _confidence_band(ins.confidence, ins.target_element) == "cautious"


def test_band_unclear_for_low_confidence():
    ins = _standard_insight(confidence=0.25)
    assert _confidence_band(ins.confidence, ins.target_element) == "unclear"


def test_band_unclear_when_label_starts_with_unclear():
    """Even at high numeric confidence, an 'unclear:' label routes to the
    unclear branch — Gemini's own escape hatch wins over its score."""
    ins = _standard_insight()
    assert ins.target_element is not None
    ins.target_element.label = "unclear: dense pricing region"
    ins.confidence = 0.9
    assert _confidence_band(ins.confidence, ins.target_element) == "unclear"


def test_band_unclear_when_target_missing():
    ins = _standard_insight(target_element=None)
    assert _confidence_band(ins.confidence, ins.target_element) == "unclear"


# ─── brain feature phrasing ─────────────────────────────────────────────────


def test_phrase_single_feature():
    assert "elevated friction" in _phrase_brain_features(["friction_anxiety"])


def test_phrase_two_features_uses_paired():
    out = _phrase_brain_features(["friction_anxiety", "cognitive_load"])
    assert "elevated friction" in out
    assert "increased cognitive effort" in out
    assert "paired with" in out


def test_phrase_three_features_uses_oxford_comma_style():
    out = _phrase_brain_features([
        "friction_anxiety", "cognitive_load", "visual_fluency",
    ])
    assert ", and " in out


def test_phrase_handles_unknown_feature():
    """Unknown features should be readable, not crash."""
    out = _phrase_brain_features(["mystery_signal"])
    # Underscores get normalised to spaces.
    assert "mystery signal" in out


def test_phrase_empty():
    assert _phrase_brain_features([]) == "an unexpected response"


# ─── standard branch render ─────────────────────────────────────────────────


def test_standard_render_contains_load_bearing_sections():
    """The standard prompt MUST contain every section a coding agent
    needs to act. Removing any of these is a contract break."""
    ins = _standard_insight()
    out = render_agent_prompt(ins, goal="evaluate the pricing flow")

    # Header
    assert "# UX fix from neural-response analysis" in out
    # Brain context phrasing — features rendered in NL
    assert "lowered reward anticipation" in out or "reward" in out
    # Goal threading
    assert "evaluate the pricing flow" in out
    # Element block
    assert "Primary CTA — Start free trial" in out
    assert "\"Start free trial\"" in out
    assert "upper-right of hero section" in out
    assert "under 'Pricing' heading" in out
    # Change block
    assert "Filled primary background" in out
    assert "hierarchy" in out
    # Acceptance criteria as checkboxes (primes the agent toward verification)
    assert "- [ ]" in out
    assert "primary CTA has higher contrast" in out
    # Codebase-search guidance — load-bearing for agent success
    assert "Search for the string \"Start free trial\"" in out
    # Permission to deviate clause
    assert "prefer the criteria" in out


def test_standard_render_no_marketing_language():
    """The prompt template itself must not use product-marketing words —
    those train agents toward fluff."""
    ins = _standard_insight()
    out = render_agent_prompt(ins, goal=None).lower()
    for word in ("delightful", "intuitive", "seamless", "frictionless",
                 "leverage", "robust"):
        assert word not in out, f"template leaked marketing word '{word}'"


def test_standard_render_handles_missing_visible_text():
    """Element with no visible text — anchors must carry the search."""
    ins = _standard_insight()
    assert ins.target_element is not None
    ins.target_element.visible_text = None
    out = render_agent_prompt(ins, goal=None)
    assert "no visible text" in out
    assert "elements matching the visual description" in out


def test_render_is_deterministic():
    """Same input → identical output. Required for the version-control
    contract — diff between revisions reflects template changes only."""
    ins = _standard_insight()
    a = render_agent_prompt(ins, goal="X")
    b = render_agent_prompt(ins, goal="X")
    assert a == b


# ─── cautious branch render ─────────────────────────────────────────────────


def test_cautious_render_includes_verify_warning():
    ins = _standard_insight(confidence=0.55)
    out = render_agent_prompt(ins, goal=None)
    assert "Medium-confidence" in out
    assert "verify the element matches" in out


def test_cautious_render_keeps_full_structure():
    """Cautious is the standard prompt + a warning line — every element
    of the standard render MUST still be present."""
    ins = _standard_insight(confidence=0.55)
    out = render_agent_prompt(ins, goal=None)
    assert "Acceptance criteria" in out
    assert "Search for the string" in out


# ─── unclear branch render ──────────────────────────────────────────────────


def test_unclear_render_for_low_confidence():
    ins = _standard_insight(confidence=0.25)
    out = render_agent_prompt(ins, goal=None)
    assert "low confidence" in out.lower() or "investigate" in out.lower()
    # Should NOT include the "Search for the string" prescriptive guidance —
    # the agent is being asked to investigate, not commit.
    assert "Search for the string" not in out


def test_unclear_render_for_unclear_label():
    ins = _standard_insight()
    assert ins.target_element is not None
    ins.target_element.label = "unclear: dense pricing region"
    ins.target_element.visual_anchors = [
        "Pro tier price column",
        "Compare plans link below the table",
    ]
    out = render_agent_prompt(ins, goal="evaluate pricing")
    assert "Candidate elements" in out
    assert "Pro tier price column" in out
    assert "Compare plans link" in out


def test_unclear_render_when_target_missing():
    ins = _standard_insight(target_element=None, proposed_change=None)
    out = render_agent_prompt(ins, goal=None)
    # Still produces a usable prompt — degrades to investigation mode.
    assert "investigate" in out.lower() or "Investigate" in out


# ─── escape hatches ─────────────────────────────────────────────────────────


def test_render_never_raises_on_minimal_insight():
    """Even a barely-populated insight must produce a non-empty string —
    the synthesizer's enrichment loop catches exceptions defensively, but
    the renderer itself is supposed to degrade gracefully."""
    ins = Insight(
        timestamp_range_s=(0.0, 1.5),
        ux_observation="something happened",
        recommendation="look at it",
        cited_brain_features=[],
        cited_screen_moment="",
        target_element=None,
        proposed_change=None,
        acceptance_criteria=[],
        confidence=0.0,
    )
    out = render_agent_prompt(ins, goal=None)
    assert isinstance(out, str)
    assert len(out) > 100
