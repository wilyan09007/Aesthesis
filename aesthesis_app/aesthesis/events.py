"""Deterministic event extraction from a TRIBE timeline.

DESIGN.md §4.5 step 1 lists 7 event types:
    spike            : delta > k * sigma (k=1.5) — handled per-TR by step7_timeline
    dominant_shift   : the dominant ROI changed since last TR
    sustained        : same ROI dominant for ≥ 3 TRs in a row
    co_movement      : a PAIRS_UX pair both rose or both fell in the same TR
    trough           : appeal_index < -0.3 AND friction_anxiety > 0.7
    flow             : flow_state window composite is True
    bounce_risk      : bounce_risk window composite is True

We mine these directly from the per-TR `frames` and per-window `windows`
the TRIBE service returned. No new neural state computation here — just
pattern matching on already-derived features. Output is a flat list of
`Event` objects (DESIGN.md §4.5 input schema for the synthesizer call).

We cap the number of events per video at `EVENT_CAP` (default 15 — the
upper end of the §4.5 "8-15 events per 60s" target). When we exceed the
cap, we keep the highest-magnitude events and the head of each event class
to preserve diversity.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

from .schemas import Event, VersionTag

log = logging.getLogger(__name__)

#: Per-video cap. DESIGN.md §4.5 step 1 says 8-15 / 60s.
EVENT_CAP: int = 15

#: How many TRs of consecutive dominance constitute "sustained".
SUSTAINED_TRS: int = 3


def _trough_event(frame: dict, version: VersionTag) -> Event | None:
    composites = frame.get("composites", {})
    appeal = composites.get("appeal_index", 0.0)
    friction = frame.get("values", {}).get("friction_anxiety", 0.0)
    if appeal < -0.3 and friction > 0.7:
        return Event(
            version=version,
            timestamp_s=frame["t_s"],
            type="trough",
            primary_roi="friction_anxiety",
            magnitude=float(friction - appeal),
            co_events=["trough_appeal_index", "high_friction_anxiety"],
        )
    return None


def _spike_events(frame: dict, version: VersionTag) -> Iterable[Event]:
    """Each spiking ROI on this TR becomes one Event."""
    spikes: dict[str, bool] = frame.get("spikes", {})
    deltas: dict[str, float] = frame.get("deltas", {})
    for roi, is_spike in spikes.items():
        if not is_spike:
            continue
        magnitude = abs(deltas.get(roi, 0.0))
        yield Event(
            version=version,
            timestamp_s=frame["t_s"],
            type="spike",
            primary_roi=roi,
            magnitude=magnitude,
            co_events=_co_event_tags(frame, roi),
        )


def _co_event_tags(frame: dict, primary_roi: str) -> list[str]:
    """Build human-readable co-event labels for one frame."""
    tags: list[str] = []
    if frame.get("dominant_shift"):
        tags.append(f"dominant_shift_to_{frame.get('dominant', '?')}")
    co = frame.get("co_movement", {})
    for pair, fired in co.items():
        if fired and primary_roi in pair:
            tags.append(f"co_movement.{pair}")
    return tags


def _dominant_shift_event(frame: dict, version: VersionTag, prev_dominant: str | None) -> Event | None:
    if not frame.get("dominant_shift"):
        return None
    new_dom = frame.get("dominant", "?")
    return Event(
        version=version,
        timestamp_s=frame["t_s"],
        type="dominant_shift",
        primary_roi=new_dom,
        magnitude=float(frame.get("values", {}).get(new_dom, 0.0)),
        co_events=[f"from_{prev_dominant}_to_{new_dom}"] if prev_dominant else [],
    )


def _sustained_events(
    frames: list[dict],
    version: VersionTag,
) -> list[Event]:
    """Find runs of ≥ SUSTAINED_TRS where the same ROI was dominant.

    Emits ONE event per run, anchored at the run's midpoint, magnitude =
    mean activation of the dominant ROI over the run.
    """
    out: list[Event] = []
    if not frames:
        return out
    run_roi = frames[0].get("dominant")
    run_start = 0
    for i, frame in enumerate(frames[1:], start=1):
        dom = frame.get("dominant")
        if dom == run_roi:
            continue
        run_len = i - run_start
        if run_len >= SUSTAINED_TRS and run_roi:
            mid = frames[run_start + run_len // 2]
            mean_mag = sum(
                f.get("values", {}).get(run_roi, 0.0)
                for f in frames[run_start:run_start + run_len]
            ) / run_len
            out.append(Event(
                version=version,
                timestamp_s=mid["t_s"],
                type="sustained",
                primary_roi=run_roi,
                magnitude=float(mean_mag),
                co_events=[f"run_length_{run_len}_TRs",
                           f"t_start_{frames[run_start]['t_s']}",
                           f"t_end_{frames[run_start + run_len - 1]['t_s']}"],
            ))
        run_roi = dom
        run_start = i
    # Tail
    run_len = len(frames) - run_start
    if run_len >= SUSTAINED_TRS and run_roi:
        mid = frames[run_start + run_len // 2]
        mean_mag = sum(
            f.get("values", {}).get(run_roi, 0.0)
            for f in frames[run_start:]
        ) / run_len
        out.append(Event(
            version=version,
            timestamp_s=mid["t_s"],
            type="sustained",
            primary_roi=run_roi,
            magnitude=float(mean_mag),
            co_events=[f"run_length_{run_len}_TRs"],
        ))
    return out


def _co_movement_events(frames: list[dict], version: VersionTag) -> list[Event]:
    """Each TR fires at most ONE co_movement event, choosing the pair with
    the strongest combined |delta|. Avoids 7 nearly-identical events per TR."""
    out: list[Event] = []
    for frame in frames:
        co = frame.get("co_movement", {})
        deltas = frame.get("deltas", {})
        if not co:
            continue
        best_pair = None
        best_mag = 0.0
        for pair, fired in co.items():
            if not fired:
                continue
            a, b = pair.split("_to_") if "_to_" in pair else (None, None)
            # Recover ROI names from PAIRS_UX layout via the frame deltas:
            mag = 0.0
            for roi, d in deltas.items():
                if roi in pair:
                    mag += abs(d)
            if mag > best_mag:
                best_mag = mag
                best_pair = pair
        if best_pair and best_mag > 0.5:
            out.append(Event(
                version=version,
                timestamp_s=frame["t_s"],
                type="co_movement",
                primary_roi=None,
                magnitude=float(best_mag),
                co_events=[f"pair.{best_pair}"],
            ))
    return out


def _window_events(windows: list[dict], version: VersionTag) -> list[Event]:
    """Mine `flow_state` and `bounce_risk` triggers from the windowed pass."""
    out: list[Event] = []
    for w in windows:
        c = w.get("composites", {})
        if c.get("flow_state"):
            out.append(Event(
                version=version,
                timestamp_s=(w["t_start_s"] + w["t_end_s"]) / 2.0,
                type="flow",
                primary_roi=None,
                magnitude=1.0,
                co_events=[f"window_{w['t_start_s']:.1f}_to_{w['t_end_s']:.1f}s"],
            ))
        if c.get("bounce_risk"):
            out.append(Event(
                version=version,
                timestamp_s=(w["t_start_s"] + w["t_end_s"]) / 2.0,
                type="bounce_risk",
                primary_roi=None,
                magnitude=1.0,
                co_events=[f"window_{w['t_start_s']:.1f}_to_{w['t_end_s']:.1f}s"],
            ))
    return out


def extract_events(timeline: dict, version: VersionTag) -> list[Event]:
    """Mine events from a /process_video_timeline response.

    Caps the output at EVENT_CAP, preserving:
        1. all `flow` and `bounce_risk` events (window-level signals are
           rare and high-signal),
        2. all `trough` events (also rare, also high-signal),
        3. the highest-magnitude `spike` / `co_movement` / `dominant_shift`
           / `sustained` events to fill the rest.
    """
    frames: list[dict] = timeline.get("frames", [])
    windows: list[dict] = timeline.get("windows", [])
    log.debug(
        "extract_events begin",
        extra={"step": "events", "version": version,
               "n_frames": len(frames), "n_windows": len(windows)},
    )

    raw: list[Event] = []

    # Per-frame events
    prev_dominant: str | None = None
    for frame in frames:
        raw.extend(_spike_events(frame, version))
        ds = _dominant_shift_event(frame, version, prev_dominant)
        if ds is not None:
            raw.append(ds)
        prev_dominant = frame.get("dominant")
        t = _trough_event(frame, version)
        if t is not None:
            raw.append(t)

    raw.extend(_sustained_events(frames, version))
    raw.extend(_co_movement_events(frames, version))
    raw.extend(_window_events(windows, version))

    # Stable sort by timestamp first so cap-trimming is deterministic.
    raw.sort(key=lambda e: (e.timestamp_s, e.type))

    # Tier-aware truncation.
    if len(raw) <= EVENT_CAP:
        log.info(
            "events extracted",
            extra={"step": "events", "version": version, "n_events": len(raw)},
        )
        return raw

    high_signal_types = {"flow", "bounce_risk", "trough"}
    pinned = [e for e in raw if e.type in high_signal_types]
    rest = [e for e in raw if e.type not in high_signal_types]
    rest.sort(key=lambda e: (-e.magnitude, e.timestamp_s))

    keep: list[Event] = []
    seen_keys: set[tuple] = set()
    by_type: dict[str, int] = defaultdict(int)

    # First take the high-signal events (preserve diversity).
    for e in pinned:
        key = (e.type, round(e.timestamp_s, 1), e.primary_roi)
        if key in seen_keys:
            continue
        keep.append(e)
        seen_keys.add(key)
        by_type[e.type] += 1
        if len(keep) >= EVENT_CAP:
            break

    # Fill remainder with highest-magnitude rest, soft cap of 4 per type.
    for e in rest:
        if len(keep) >= EVENT_CAP:
            break
        if by_type[e.type] >= 4:
            continue
        key = (e.type, round(e.timestamp_s, 1), e.primary_roi)
        if key in seen_keys:
            continue
        keep.append(e)
        seen_keys.add(key)
        by_type[e.type] += 1

    keep.sort(key=lambda e: e.timestamp_s)
    log.info(
        "events extracted (capped)",
        extra={"step": "events", "version": version,
               "n_events": len(keep), "n_raw": len(raw)},
    )
    return keep
