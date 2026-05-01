"""Orchestrator screenshot fan-out — real ffmpeg, no mocks.

The orchestrator's ``_attach_screenshots`` used to walk events in a
sequential Python loop on the asyncio thread, which on Modal stacked up
to ~30s of ffmpeg subprocess calls per request. The fix fans them out
across ``asyncio.to_thread`` so the wall time is bounded by the slowest
single extraction, not the sum.

These tests exercise the real path: a synthetic MP4 generated via
``ffmpeg lavfi``, real ``extract_frame`` calls, real subprocess. The
parallelism check compares wall-clock time against a sequential baseline
with the same number of extractions; if the fan-out regresses, the test
trips.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from aesthesis.orchestrator import _attach_screenshots
from aesthesis.schemas import Event
from aesthesis.screenshots import extract_frame


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.fail(
            "ffmpeg binary not on PATH — these orchestrator screenshot "
            "tests refuse to mock the binary away."
        )


def _make_test_video(out: Path, duration_s: float = 8.0) -> Path:
    _require_ffmpeg()
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_s:.2f}:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=30)
    if res.returncode != 0 or not out.exists():
        pytest.fail(
            f"could not synthesise test MP4 (rc={res.returncode}): "
            f"{res.stderr.decode(errors='replace')[:400]}"
        )
    return out


def _event(t_s: float) -> Event:
    return Event(
        timestamp_s=t_s,
        type="spike",
        primary_roi="reward_anticipation",
        magnitude=1.0,
        co_events=[],
    )


# ─── Behaviour: attaches screenshot_path on success ──────────────────────────

def test_attach_screenshots_sets_paths_on_each_event(tmp_path: Path):
    video = _make_test_video(tmp_path / "src.mp4")
    work_dir = tmp_path / "frames"
    events = [_event(0.5), _event(1.5), _event(3.0)]

    asyncio.run(_attach_screenshots(
        events, video, work_dir=work_dir, run_id="test-paths",
    ))

    for e in events:
        assert e.screenshot_path is not None, f"no screenshot for t={e.timestamp_s}"
        p = Path(e.screenshot_path)
        assert p.exists() and p.stat().st_size > 0


def test_attach_screenshots_no_op_on_empty_events(tmp_path: Path):
    """Empty event list must not blow up nor create the work dir
    (small but real Modal-side guarantee — orchestrator skips this
    when there's nothing to do)."""
    work_dir = tmp_path / "frames"
    asyncio.run(_attach_screenshots(
        [], tmp_path / "no_video.mp4", work_dir=work_dir, run_id="empty",
    ))
    # Empty events → we shouldn't have touched the work dir.
    assert not work_dir.exists()


def test_attach_screenshots_swallows_per_event_failures(tmp_path: Path):
    """If a single event has a bad timestamp, the others should still get
    their screenshots. ``_attach_screenshots`` must never raise."""
    video = _make_test_video(tmp_path / "src.mp4", duration_s=3.0)
    work_dir = tmp_path / "frames"
    events = [
        _event(0.5),       # in range → succeeds
        _event(999.0),     # past EOF → fails silently
        _event(1.5),       # in range → succeeds
    ]

    asyncio.run(_attach_screenshots(
        events, video, work_dir=work_dir, run_id="mixed",
    ))

    assert events[0].screenshot_path is not None
    assert events[2].screenshot_path is not None
    # The failed event must NOT have a stale path attached.
    assert events[1].screenshot_path is None


# ─── Performance: parallel must beat sequential ──────────────────────────────

def _measure_one_extraction(video: Path, tmp_path: Path) -> float:
    """Real wall-clock for a single extract_frame call against the video.
    Used to derive the parallelism threshold."""
    out = tmp_path / "warmup.jpg"
    t0 = time.perf_counter()
    extract_frame(video, 1.0, out)
    return time.perf_counter() - t0


def test_attach_screenshots_runs_in_parallel(tmp_path: Path):
    """Wall-clock for N extractions must be far less than N × single.

    This is the load-bearing perf invariant: the previous serial loop
    produced N × per-call latency. The fan-out must produce something
    closer to per-call latency on its own. We use 6 events and require
    the total to be under 0.6 × (6 × single) — generous enough to
    survive CI jitter, tight enough to catch a regression to serial.
    """
    video = _make_test_video(tmp_path / "src.mp4", duration_s=8.0)
    # Warm ffmpeg + filesystem caches first so the timing is stable.
    single_ms = _measure_one_extraction(video, tmp_path) * 1000.0

    n = 6
    events = [_event(0.5 + i * 1.0) for i in range(n)]
    work_dir = tmp_path / "parallel_frames"

    t0 = time.perf_counter()
    asyncio.run(_attach_screenshots(
        events, video, work_dir=work_dir, run_id="parallel",
    ))
    parallel_ms = (time.perf_counter() - t0) * 1000.0

    serial_budget_ms = n * single_ms
    # Tight enough to fail if someone re-serialises the loop.
    threshold = 0.6 * serial_budget_ms
    assert parallel_ms < threshold, (
        f"_attach_screenshots looks serial: {parallel_ms:.0f}ms for {n} "
        f"events, single={single_ms:.0f}ms, "
        f"threshold={threshold:.0f}ms (=0.6 × {serial_budget_ms:.0f}ms)"
    )

    # And every event was actually screenshotted.
    for e in events:
        assert e.screenshot_path is not None
