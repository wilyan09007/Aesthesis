"""TRIBE service constants.

Frozen at module import. All other modules read from here so there's
exactly one place to change a number.

References:
- DESIGN.md §5.4 / §5.16 (UX-tuned 8-ROI keyset, canonical)
- DESIGN.md §5.16.5 (PAIRS_UX connectivity)
- DESIGN.md §5.16.3 / §5.16.4 (composite formulas)
"""

from __future__ import annotations

# ─── TRIBE model output shape ────────────────────────────────────────────────

#: Repetition time. Each TRIBE prediction frame covers this many seconds.
#: DESIGN.md §5.4: TR_DURATION = 1.5
#:
#: NOTE: TRIBE's actual training TR is 1.49s. The design doc rounds to 1.5s.
#: The prediction array is offset 5s "into the past" by the model itself
#: (DESIGN.md §10 D3) so absolute timestamps `t * TR_DURATION` already align
#: to stimulus time — no further shift needed downstream.
TR_DURATION: float = 1.5

#: fsaverage5 cortical mesh size (vertices). Both hemispheres combined.
#: DESIGN.md §5.4 / §5.15.2.
NUM_VERTICES: int = 20_484

#: Edge-TR trim count. DESIGN.md §5.6 #4 / §5.15.5: removes Transformer
#: boundary artifact at the tail of the prediction. The video pathway needs
#: empirical re-validation (still applies / not), but defaulting to "trim"
#: matches the reference and is conservative.
EDGE_TR_TRIM: int = 2

# ─── 8 UX-tuned ROIs ─────────────────────────────────────────────────────────

#: Maps each UX ROI name to a list of (Yeo network mask substring, Neurosynth
#: weight-map term or None) pairs. DESIGN.md §5.16.2.
#:
#: For a given ROI, `extract_all` averages across cortical vertices that fall
#: in ANY of the listed networks, weighted by the per-vertex Neurosynth term
#: weight. `None` means "uniform weight (1.0) across the network mask."
NETWORK_KEYS_UX: dict[str, list[tuple[str, str | None]]] = {
    "aesthetic_appeal":    [("_Default_",     "memory"), ("_Limbic_", "reward")],
    "visual_fluency":      [("_Vis_",         None)],
    "cognitive_load":      [("_Cont_",        "conflict"), ("_DorsAttn_", "uncertainty")],
    "trust_affinity":      [("_Default_",     "social")],
    "reward_anticipation": [("_Limbic_",      "reward")],
    "motor_readiness":     [("_SomMot_",      "motor")],
    "surprise_novelty":    [("_SalVentAttn_", None),     ("_Cont_",      None)],
    "friction_anxiety":    [("_SalVentAttn_", "fear")],
}

#: Canonical key order. Used for stable dict serialization and the per-TR
#: matrix column order. Do not change without coordinating with the app.
ROI_KEYS: tuple[str, ...] = tuple(NETWORK_KEYS_UX.keys())

#: Yeo network substrings actually referenced. Used by init_resources
#: to skip building masks we don't need.
YEO_NETWORK_SUBSTRINGS: tuple[str, ...] = (
    "_Default_",
    "_Limbic_",
    "_Vis_",
    "_Cont_",
    "_DorsAttn_",
    "_SomMot_",
    "_SalVentAttn_",
)

#: Neurosynth terms whose ALE meta-analysis weight maps must be projected to
#: fsaverage5. DESIGN.md §5.8 generates this from NiMARE.
NEUROSYNTH_TERMS: tuple[str, ...] = (
    "fear", "reward", "uncertainty", "conflict", "social", "motor", "memory",
)

# ─── Connectivity pairs ──────────────────────────────────────────────────────

#: 7 named Pearson pairs computed per window. DESIGN.md §5.16.5.
PAIRS_UX: dict[str, tuple[str, str]] = {
    "appeal_to_action":       ("aesthetic_appeal",    "motor_readiness"),
    "reward_to_action":       ("reward_anticipation", "motor_readiness"),
    "load_to_friction":       ("cognitive_load",      "friction_anxiety"),
    "trust_to_appeal":        ("trust_affinity",      "aesthetic_appeal"),
    "fluency_to_appeal":      ("visual_fluency",      "aesthetic_appeal"),
    "surprise_to_load":       ("surprise_novelty",    "cognitive_load"),
    "friction_blocks_motor":  ("friction_anxiety",    "motor_readiness"),
}

# ─── Window sizes ────────────────────────────────────────────────────────────

#: Default sliding window in TRs. 4 TRs = 6s. DESIGN.md §5.10.
WINDOW_TRS_DEFAULT: int = 4

#: Default step in TRs. 1 TR = 1.5s, so a new window every 1.5s.
STEP_TRS_DEFAULT: int = 1

#: For std-based composites (`flow_state` etc.), use a wider window.
#: DESIGN.md `/plan-eng-review` R2: n=4 is too noisy for std()-based gates;
#: 6 TRs = 9s is the sweet spot. Other composites still use 4 TRs.
WINDOW_TRS_STD: int = 6

# ─── Spike detection ─────────────────────────────────────────────────────────

#: An ROI fires a "spike" when its delta exceeds k * sigma_delta_roi.
#: DESIGN.md §5.3 B1 frame structure.
SPIKE_K: float = 1.5
