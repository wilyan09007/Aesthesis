"""Per-TR composite formulas — pure-function tests.

DESIGN.md `/plan-eng-review` R5 mandated unit tests for all 8 per-TR
composites. These verify the published mechanism each formula encodes.
"""

from __future__ import annotations

import math

import pytest

from tribe_neural.steps.step5_composites import (
    appeal_index,
    aesthetic_dwell,
    bounce_risk,
    conversion_intent,
    decision_clarity,
    engagement_depth,
    flow_state,
    fluency_score,
    friction_burst,
    hook_strength,
    memorability_proxy,
    surprise_polarity,
    trust_index,
    ux_dominance,
)

import numpy as np


# Convenience builder — all 8 ROIs = 0 unless overridden.
def vals(**overrides: float) -> dict[str, float]:
    base = {
        "aesthetic_appeal": 0.0,
        "visual_fluency": 0.0,
        "cognitive_load": 0.0,
        "trust_affinity": 0.0,
        "reward_anticipation": 0.0,
        "motor_readiness": 0.0,
        "surprise_novelty": 0.0,
        "friction_anxiety": 0.0,
    }
    base.update(overrides)
    return base


# ─── appeal_index ────────────────────────────────────────────────────────────

def test_appeal_index_pure_appeal():
    # only DMN aesthetic firing — should equal 0.45 weight.
    assert appeal_index(vals(aesthetic_appeal=1.0)) == pytest.approx(0.45)


def test_appeal_index_load_kills_appeal():
    # high load reduces appeal even at high aesthetic baseline
    high_load = appeal_index(vals(aesthetic_appeal=1.0, cognitive_load=1.0))
    low_load = appeal_index(vals(aesthetic_appeal=1.0, cognitive_load=0.0))
    assert high_load < low_load


def test_appeal_index_friction_kills_appeal():
    high_friction = appeal_index(vals(aesthetic_appeal=1.0, friction_anxiety=1.0))
    no_friction = appeal_index(vals(aesthetic_appeal=1.0, friction_anxiety=0.0))
    assert high_friction < no_friction


# ─── conversion_intent ───────────────────────────────────────────────────────

def test_conversion_intent_zero_motor():
    # No motor readiness => no clickable state regardless of reward.
    assert conversion_intent(vals(reward_anticipation=1.0, motor_readiness=0.0)) == 0.0


def test_conversion_intent_high_friction_zeroes():
    # If insula > NAcc, drive is negative -> output 0 (max-clamp).
    out = conversion_intent(vals(
        reward_anticipation=0.2, friction_anxiety=1.0, motor_readiness=1.0,
    ))
    assert out == 0.0


def test_conversion_intent_full_path():
    out = conversion_intent(vals(
        reward_anticipation=1.0, friction_anxiety=0.0, motor_readiness=1.0,
    ))
    assert out == pytest.approx(1.0, rel=1e-3)


# ─── fluency_score ───────────────────────────────────────────────────────────

def test_fluency_score_inverts_load():
    """Reber 2004 — fluency = ease minus effort."""
    high = fluency_score(vals(visual_fluency=1.0, cognitive_load=0.0))
    low = fluency_score(vals(visual_fluency=1.0, cognitive_load=1.0))
    assert high > low


# ─── trust_index ─────────────────────────────────────────────────────────────

def test_trust_index_friction_subtracts():
    """Mende-Siedlecki — vmPFC integration minus amygdala threat."""
    safe = trust_index(vals(trust_affinity=1.0, friction_anxiety=0.0))
    threat = trust_index(vals(trust_affinity=1.0, friction_anxiety=1.0))
    assert safe > threat
    assert safe - threat == pytest.approx(0.6)  # the constant in §5.16.3


# ─── engagement_depth ────────────────────────────────────────────────────────

def test_engagement_depth_yerkes_dodson_peak():
    """Should peak at load = 0.5 (Yerkes-Dodson inverted-U)."""
    at_peak = engagement_depth(vals(aesthetic_appeal=1.0, cognitive_load=0.5))
    no_load = engagement_depth(vals(aesthetic_appeal=1.0, cognitive_load=0.0))
    full_load = engagement_depth(vals(aesthetic_appeal=1.0, cognitive_load=1.0))
    assert at_peak > no_load
    assert at_peak > full_load


def test_engagement_depth_zero_appeal_zeros():
    """No aesthetic component -> no engagement regardless of load."""
    for load in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert engagement_depth(vals(aesthetic_appeal=0.0, cognitive_load=load)) == 0.0


# ─── surprise_polarity ───────────────────────────────────────────────────────

def test_surprise_polarity_positive_when_reward_dominates():
    pol = surprise_polarity(vals(
        surprise_novelty=1.0, reward_anticipation=1.0, friction_anxiety=0.0,
    ))
    assert pol > 0


def test_surprise_polarity_negative_when_friction_dominates():
    pol = surprise_polarity(vals(
        surprise_novelty=1.0, reward_anticipation=0.0, friction_anxiety=1.0,
    ))
    assert pol < 0


def test_surprise_polarity_zero_surprise_zero_output():
    pol = surprise_polarity(vals(
        surprise_novelty=0.0, reward_anticipation=1.0, friction_anxiety=0.0,
    ))
    assert pol == 0.0


# ─── memorability_proxy ──────────────────────────────────────────────────────

def test_memorability_proxy_requires_both_components():
    only_appeal = memorability_proxy(vals(aesthetic_appeal=1.0, surprise_novelty=0.0))
    only_surprise = memorability_proxy(vals(aesthetic_appeal=0.0, surprise_novelty=1.0))
    both = memorability_proxy(vals(aesthetic_appeal=1.0, surprise_novelty=1.0))
    assert only_appeal == 0.0
    assert only_surprise == 0.0
    assert both == 1.0


# ─── ux_dominance ────────────────────────────────────────────────────────────

def test_ux_dominance_sign():
    pos = ux_dominance(vals(aesthetic_appeal=1.0, reward_anticipation=1.0))
    neg = ux_dominance(vals(cognitive_load=1.0, friction_anxiety=1.0))
    bal = ux_dominance(vals(aesthetic_appeal=1.0, cognitive_load=1.0))
    assert pos > 0
    assert neg < 0
    assert bal == pytest.approx(0.0, abs=1e-3)


def test_ux_dominance_bounded_minus_one_to_one():
    # extreme positive
    pos = ux_dominance(vals(aesthetic_appeal=1.0, reward_anticipation=1.0))
    # extreme negative
    neg = ux_dominance(vals(cognitive_load=1.0, friction_anxiety=1.0))
    assert -1.0 - 1e-3 <= pos <= 1.0 + 1e-3
    assert -1.0 - 1e-3 <= neg <= 1.0 + 1e-3


def test_ux_dominance_no_signal_safe():
    # All zero -> must not divide-by-zero.
    out = ux_dominance(vals())
    assert math.isfinite(out)
    assert abs(out) < 1e-3


# ─── window composites ──────────────────────────────────────────────────────

def test_flow_state_triggers_on_steady_high_engagement():
    eng = np.full(6, 0.8)
    app = np.full(6, 0.5)
    assert flow_state({"engagement_depth": eng, "appeal_index": app}) is True


def test_flow_state_skips_when_jittery():
    eng = np.full(6, 0.8)
    app = np.array([0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
    assert flow_state({"engagement_depth": eng, "appeal_index": app}) is False


def test_flow_state_skips_when_small_window():
    eng = np.full(2, 0.8)
    app = np.full(2, 0.5)
    assert flow_state({"engagement_depth": eng, "appeal_index": app}) is False


def test_decision_clarity_motor_up_load_down():
    motor = np.linspace(0.0, 1.0, 6)
    load = np.linspace(1.0, 0.0, 6)
    assert decision_clarity({}, motor=motor, load=load) is True


def test_decision_clarity_correlated_doesnt_trigger():
    motor = np.linspace(0.0, 1.0, 6)
    load = np.linspace(0.0, 1.0, 6)
    assert decision_clarity({}, motor=motor, load=load) is False


def test_bounce_risk_triggers():
    load = np.full(6, 1.5)
    friction = np.full(6, 1.5)
    motor = np.full(6, 0.1)
    assert bounce_risk({}, load=load, friction=friction, motor=motor) is True


def test_bounce_risk_doesnt_trigger_when_motor_active():
    load = np.full(6, 1.5)
    friction = np.full(6, 1.5)
    motor = np.array([0.0, 0.5, 0.0, 0.5, 0.0, 0.5])  # high std
    assert bounce_risk({}, load=load, friction=friction, motor=motor) is False


def test_hook_strength_zero_outside_first_window():
    app = np.array([0.5, 0.4, 0.3, 0.2])
    assert hook_strength({"appeal_index": app}, is_first_window=False) == 0.0


def test_hook_strength_picks_peak_in_first_three():
    app = np.array([0.2, 0.7, 0.4, 0.1])
    assert hook_strength({"appeal_index": app}, is_first_window=True) == pytest.approx(0.7)


def test_aesthetic_dwell_three_in_a_row():
    appeal = np.array([0.6, 0.7, 0.8, 0.0, 0.0, 0.0])
    assert aesthetic_dwell({"aesthetic_appeal_raw": appeal}) is True


def test_aesthetic_dwell_no_streak():
    appeal = np.array([0.6, 0.0, 0.7, 0.0, 0.6, 0.0])
    assert aesthetic_dwell({"aesthetic_appeal_raw": appeal}) is False


def test_friction_burst_negative_polarity_plus_friction_spike():
    pol = np.array([-0.6, -0.7, -0.5, 0.0])
    friction = np.array([0.0, 1.5, 0.5, 0.0])
    assert friction_burst({"surprise_polarity": pol, "friction_anxiety_raw": friction}) is True


def test_friction_burst_doesnt_trigger_on_small_negative():
    pol = np.array([-0.3, -0.2, 0.0])
    friction = np.array([0.0, 0.5, 0.0])
    assert friction_burst({"surprise_polarity": pol, "friction_anxiety_raw": friction}) is False
