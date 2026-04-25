"""D15 — orchestrator action-log integration tests.

NO MOCKS. Tests use real ``Event`` objects, real on-disk ``actions.jsonl``
files in ``tmp_path``, and the real loader / nearest-action functions.
The ``_attach_per_event_context`` test exercises action-stamping logic
without invoking ffmpeg (it would also extract screenshots, but we
intentionally point at a non-existent video so screenshot extraction
silently no-ops — the action-stamping path still runs).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aesthesis.orchestrator import (
    _ACTION_MATCH_WINDOW_S,
    _attach_per_event_context,
    _load_action_log,
    _nearest_action,
)
from aesthesis.schemas import Event


# ─── _load_action_log ──────────────────────────────────────────────────────


def test_load_action_log_none_path_returns_empty() -> None:
    actions = _load_action_log(None, run_id="t1")
    assert actions == []


def test_load_action_log_missing_file_returns_empty(tmp_path: Path) -> None:
    """Skip-path callers may pass a path that doesn't exist if capture
    didn't run — we should treat that as 'no actions' rather than crash."""
    missing = tmp_path / "nope.jsonl"
    actions = _load_action_log(missing, run_id="t1")
    assert actions == []


def test_load_action_log_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "actions.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"i": 0, "timestamp_s": 1.5, "description": "click hero CTA"}),
            json.dumps({"i": 1, "timestamp_s": 4.2, "description": "scroll to pricing"}),
            json.dumps({"i": 2, "timestamp_s": 7.0, "description": "fill email field"}),
        ]),
        encoding="utf-8",
    )
    actions = _load_action_log(p, run_id="t1")
    assert len(actions) == 3
    # Returned in timestamp order
    assert actions[0]["timestamp_s"] == 1.5
    assert actions[2]["timestamp_s"] == 7.0
    assert actions[1]["description"] == "scroll to pricing"


def test_load_action_log_skips_malformed_lines(tmp_path: Path) -> None:
    """A capture subprocess crash mid-write could leave a partial last
    line. We must skip with a warning, not raise."""
    p = tmp_path / "actions.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"timestamp_s": 1.0, "description": "good"}),
            "this is not valid json",
            "{partial",
            json.dumps({"timestamp_s": 5.0, "description": "also good"}),
            json.dumps({"description": "no timestamp_s field"}),  # ts becomes 0.0
        ]),
        encoding="utf-8",
    )
    actions = _load_action_log(p, run_id="t1")
    # 2 valid + 1 with default ts=0 = 3
    assert len(actions) == 3
    descriptions = [a["description"] for a in actions]
    assert "good" in descriptions
    assert "also good" in descriptions


def test_load_action_log_drops_empty_descriptions(tmp_path: Path) -> None:
    p = tmp_path / "actions.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"timestamp_s": 1.0, "description": "real action"}),
            json.dumps({"timestamp_s": 2.0, "description": "   "}),  # whitespace
            json.dumps({"timestamp_s": 3.0, "description": ""}),
        ]),
        encoding="utf-8",
    )
    actions = _load_action_log(p, run_id="t1")
    assert len(actions) == 1
    assert actions[0]["description"] == "real action"


# ─── _nearest_action ───────────────────────────────────────────────────────


def test_nearest_action_empty_actions_returns_none() -> None:
    assert _nearest_action([], 1.0) is None


def test_nearest_action_within_window_finds_closest() -> None:
    actions = [
        {"timestamp_s": 1.0, "description": "A"},
        {"timestamp_s": 2.0, "description": "B"},
        {"timestamp_s": 5.0, "description": "C"},
    ]
    # Closer to A than to B
    assert _nearest_action(actions, 1.1)["description"] == "A"  # type: ignore[index]
    # Right between A and B but slightly closer to B
    assert _nearest_action(actions, 1.6)["description"] == "B"  # type: ignore[index]
    # Far from everything
    assert _nearest_action(actions, 10.0) is None


def test_nearest_action_respects_window_boundary() -> None:
    """Only matches within ±_ACTION_MATCH_WINDOW_S (0.5s default)."""
    actions = [{"timestamp_s": 5.0, "description": "X"}]
    # Inside window
    assert _nearest_action(actions, 5.0 + _ACTION_MATCH_WINDOW_S - 0.01) is not None
    # Outside window
    assert _nearest_action(actions, 5.0 + _ACTION_MATCH_WINDOW_S + 0.01) is None
    assert _nearest_action(actions, 5.0 - _ACTION_MATCH_WINDOW_S - 0.01) is None


# ─── _attach_per_event_context (full integration of the action stamping) ──


def _evt(t: float) -> Event:
    return Event(timestamp_s=t, type="spike", primary_roi="cognitive_load",
                 magnitude=0.5, co_events=[])


@pytest.mark.asyncio
async def test_attach_per_event_context_stamps_actions(tmp_path: Path) -> None:
    actions_path = tmp_path / "actions.jsonl"
    actions_path.write_text(
        "\n".join([
            json.dumps({"timestamp_s": 1.0, "description": "click hero"}),
            json.dumps({"timestamp_s": 4.5, "description": "scroll down"}),
        ]),
        encoding="utf-8",
    )
    events = [_evt(1.1), _evt(4.4), _evt(10.0)]  # last one too far from any action

    # Point at a non-existent video so screenshot extraction silently no-ops;
    # we're testing the action-stamping side here, not screenshots.
    await _attach_per_event_context(
        events, video=tmp_path / "no-such-video.mp4",
        work_dir=tmp_path / "frames",
        run_id="test1",
        action_log_path=actions_path,
    )

    assert events[0].agent_action_at_t == "click hero"
    assert events[1].agent_action_at_t == "scroll down"
    assert events[2].agent_action_at_t is None


@pytest.mark.asyncio
async def test_attach_per_event_context_skip_path_unchanged(tmp_path: Path) -> None:
    """D15 regression: when action_log_path is None, events are NOT stamped.
    Skip-path (multipart upload) callers must see identical behaviour to v1."""
    events = [_evt(1.0), _evt(2.0)]
    await _attach_per_event_context(
        events, video=tmp_path / "no-such-video.mp4",
        work_dir=tmp_path / "frames",
        run_id="test1",
        action_log_path=None,
    )
    for e in events:
        assert e.agent_action_at_t is None, (
            "skip path must not stamp agent_action_at_t — D15 regression"
        )
