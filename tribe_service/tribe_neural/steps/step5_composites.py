"""Per-TR and window-scoped composite formulas.

These are pure functions of either:
- a `values` dict (one per TR — the 8 ROI activations at that frame), or
- a window of per-TR values + already-computed connectivity / stats.

Per-TR composites: DESIGN.md §5.16.3
Window composites: DESIGN.md §5.16.4
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

import numpy as np

log = logging.getLogger(__name__)

EPS = 1e-6


# ─── Per-TR composites (§5.16.3) ─────────────────────────────────────────────

def appeal_index(v: Mapping[str, float]) -> float:
    """Vessel 2019 — DMN aesthetic engagement minus cognitive cost.

    Penalize cognitive_load (workload kills aesthetic experience) and
    friction_anxiety (negative affect blocks aesthetic appreciation).
    """
    return (
        0.45 * v["aesthetic_appeal"]
        + 0.25 * v["reward_anticipation"]
        - 0.20 * v["cognitive_load"]
        - 0.10 * v["friction_anxiety"]
    )


def conversion_intent(v: Mapping[str, float]) -> float:
    """Knutson 2007 SHOP task — NAcc reward minus insula price aversion,
    gated by motor readiness.

    Returns 0 when motor readiness or net drive is non-positive (the user
    isn't in a clickable state OR price aversion dominates).
    """
    nacc = v["reward_anticipation"]
    insula = v["friction_anxiety"]
    motor = v["motor_readiness"]
    drive = (nacc - insula) / (nacc + insula + EPS)
    return motor * max(drive, 0.0)


def fluency_score(v: Mapping[str, float]) -> float:
    """Reber 2004 — processing fluency = ease of parsing minus effort to parse."""
    return v["visual_fluency"] - 0.5 * v["cognitive_load"]


def trust_index(v: Mapping[str, float]) -> float:
    """Mende-Siedlecki / FeldmanHall — vmPFC integration minus amygdala threat
    (cortically projected via friction_anxiety which carries VIFS+anterior insula)."""
    return v["trust_affinity"] - 0.6 * v["friction_anxiety"]


def engagement_depth(v: Mapping[str, float]) -> float:
    """Yerkes-Dodson — engagement peaks at moderate cognitive load.

    `optimal` is 1.0 when load == 0.5, decaying to 0 at load == 0 or load == 1.
    Multiplied by aesthetic_appeal so beauty-without-effort still reads
    as engaging, but apathy reads as zero.
    """
    load = v["cognitive_load"]
    optimal = max(0.0, 1.0 - 2.0 * abs(load - 0.5))
    return v["aesthetic_appeal"] * optimal


def surprise_polarity(v: Mapping[str, float]) -> float:
    """Direction-signed surprise. Positive = "good surprise" (delight),
    negative = "bad surprise" (shock / bait-and-switch)."""
    direction = v["reward_anticipation"] - v["friction_anxiety"]
    sign = 1.0 if direction >= 0 else -1.0
    return sign * v["surprise_novelty"]


def memorability_proxy(v: Mapping[str, float]) -> float:
    """Bainbridge 2017 — memorable = beautiful and unexpected. Cortical
    proxy until the §5.17 subcortical extension lands."""
    return v["aesthetic_appeal"] * v["surprise_novelty"]


def ux_dominance(v: Mapping[str, float]) -> float:
    """Is the user being driven by appeal or by friction? Range [-1, +1]."""
    pos = v["aesthetic_appeal"] + v["reward_anticipation"]
    neg = v["cognitive_load"] + v["friction_anxiety"]
    return (pos - neg) / (pos + neg + EPS)


PER_TR_COMPOSITES = {
    "appeal_index": appeal_index,
    "conversion_intent": conversion_intent,
    "fluency_score": fluency_score,
    "trust_index": trust_index,
    "engagement_depth": engagement_depth,
    "surprise_polarity": surprise_polarity,
    "memorability_proxy": memorability_proxy,
    "ux_dominance": ux_dominance,
}


def compute_per_tr_composites(values: Mapping[str, float]) -> dict[str, float]:
    """Apply every per-TR composite to a single frame's ROI values."""
    return {name: float(fn(values)) for name, fn in PER_TR_COMPOSITES.items()}


# ─── Window composites (§5.16.4) ─────────────────────────────────────────────
# These take a window of per-TR composite values (already computed by the
# windowed sub-pass) plus window-scoped connectivity. They emit booleans /
# scalars that say "did this UX pattern occur during this window?"

def flow_state(window: dict[str, np.ndarray], **_kw) -> bool:
    """Sustained absorbed engagement: high mean engagement_depth, low
    appeal_index variance.

    DESIGN.md /plan-eng-review R2 bumped this window to 6 TRs (9s) for
    statistical sanity — std() of 4 samples is too noisy.
    """
    eng = window.get("engagement_depth")
    app = window.get("appeal_index")
    if eng is None or app is None or eng.size < 4:
        return False
    return bool(eng.mean() > 0.6 and app.std() < 0.2)


def decision_clarity(
    window: dict[str, np.ndarray],
    *,
    motor: np.ndarray | None = None,
    load: np.ndarray | None = None,
    **_kw,
) -> bool:
    """Motor rises while load falls — "I figured it out, now I act."

    Parameters mirror the formula sketch: needs the raw motor_readiness +
    cognitive_load timeseries for the window (not just composites).
    """
    if motor is None or load is None or motor.size < 3:
        return False
    if motor.std() < 1e-9 or load.std() < 1e-9:
        return False
    r = float(np.corrcoef(motor, load)[0, 1])
    if not np.isfinite(r):
        return False
    return r < -0.3


def bounce_risk(
    window: dict[str, np.ndarray],
    *,
    load: np.ndarray | None = None,
    friction: np.ndarray | None = None,
    motor: np.ndarray | None = None,
    **_kw,
) -> bool:
    """Both load and friction elevated AND motor flat — confused user
    about to leave."""
    if load is None or friction is None or motor is None:
        return False
    if load.size < 3:
        return False
    high_load = load.mean() > 1.0
    high_friction = friction.mean() > 1.0
    # std < 0.2 — anything more energetic than a tiny jitter counts as
    # the user moving toward action and disqualifies the bounce-risk pattern.
    flat_motor = motor.std() < 0.2
    return bool(high_load and high_friction and flat_motor)


def hook_strength(
    window: dict[str, np.ndarray],
    *,
    is_first_window: bool = False,
    **_kw,
) -> float:
    """Lindgaard 2006 first-impression — appeal_index peak in the first
    3 TRs of the run (BOLD-shifted by the model's hemodynamic offset).

    Returns 0.0 except for the very first window of the run; then the peak
    value of `appeal_index` over its first 3 TRs.
    """
    if not is_first_window:
        return 0.0
    app = window.get("appeal_index")
    if app is None or app.size == 0:
        return 0.0
    head = app[: min(3, app.size)]
    return float(head.max())


def aesthetic_dwell(window: dict[str, np.ndarray], **_kw) -> bool:
    """aesthetic_appeal sustained > 0.5 sigma for ≥ 3 consecutive TRs."""
    app_raw = window.get("aesthetic_appeal_raw")
    if app_raw is None or app_raw.size < 3:
        return False
    threshold = 0.5  # raw is z-scored already, so 0.5 IS half a sigma
    flags = app_raw > threshold
    # Look for any run of 3 consecutive Trues.
    streak = 0
    for f in flags:
        streak = streak + 1 if f else 0
        if streak >= 3:
            return True
    return False


def friction_burst(window: dict[str, np.ndarray], **_kw) -> bool:
    """Bait-and-switch / dark pattern: surprise_polarity strongly negative
    AND friction_anxiety raw spike co-occurs."""
    pol = window.get("surprise_polarity")
    fric = window.get("friction_anxiety_raw")
    if pol is None or fric is None or pol.size < 2 or fric.size < 2:
        return False
    return bool(pol.min() < -0.5 and fric.max() > 1.0)


WINDOW_COMPOSITE_FNS = {
    "flow_state": flow_state,
    "decision_clarity": decision_clarity,
    "bounce_risk": bounce_risk,
    "hook_strength": hook_strength,
    "aesthetic_dwell": aesthetic_dwell,
    "friction_burst": friction_burst,
}


def compute_window_composites(
    composites_window: dict[str, np.ndarray],
    roi_window: Mapping[str, np.ndarray],
    *,
    is_first_window: bool,
) -> dict[str, float | bool]:
    """Run all 6 window composites against the per-window slice.

    Args:
        composites_window: per-TR composite values, sliced to the window.
            Keys = PER_TR_COMPOSITES names. Each value is shape (window_len,).
        roi_window: raw (z-scored) ROI values for the same window. Used by
            composites that need the underlying ROI signal directly
            (e.g., decision_clarity wants motor + load timeseries; aesthetic_dwell
            wants raw aesthetic_appeal).
        is_first_window: True only for the first window of a run, which
            unlocks `hook_strength`.

    Returns:
        Dict in WINDOW_COMPOSITE_FNS key order. Bool composites map to bool;
        scalar composites (currently just hook_strength) map to float.
    """
    # Splice raw ROI series in under specially-named keys so the composites
    # can pull what they need without us repeating the dispatch logic.
    augmented = dict(composites_window)
    for roi_name, ts in roi_window.items():
        augmented[f"{roi_name}_raw"] = ts

    motor = roi_window.get("motor_readiness")
    load = roi_window.get("cognitive_load")
    friction = roi_window.get("friction_anxiety")

    out: dict[str, float | bool] = {}
    for name, fn in WINDOW_COMPOSITE_FNS.items():
        out[name] = fn(
            augmented,
            motor=motor,
            load=load,
            friction=friction,
            is_first_window=is_first_window,
        )
    return out


__all__ = [
    "PER_TR_COMPOSITES",
    "WINDOW_COMPOSITE_FNS",
    "compute_per_tr_composites",
    "compute_window_composites",
    # Re-export individual composites so tests can target them by name.
    "appeal_index",
    "conversion_intent",
    "fluency_score",
    "trust_index",
    "engagement_depth",
    "surprise_polarity",
    "memorability_proxy",
    "ux_dominance",
    "flow_state",
    "decision_clarity",
    "bounce_risk",
    "hook_strength",
    "aesthetic_dwell",
    "friction_burst",
]
