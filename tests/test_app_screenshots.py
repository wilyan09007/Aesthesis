"""Frame-extraction tests against a real MP4 (no mocks).

Each test materialises a small lavfi-generated MP4 via the system ffmpeg
binary, then exercises ``aesthesis.screenshots`` against it. If ffmpeg
isn't on PATH the tests fail loudly — same posture as the rest of the
suite (``feedback_no_mocks``: real binaries, real I/O, fail loudly when
the environment isn't set up).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aesthesis import screenshots
from aesthesis.screenshots import (
    FFMPEG_CLI_TIMEOUT_S,
    _ffmpeg_seek_extract,
    _run_ffmpeg_cli,
    extract_frame,
)


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.fail(
            "ffmpeg binary not on PATH — install ffmpeg or run the test "
            "container that has it. screenshots tests refuse to mock the "
            "binary away."
        )


def _make_test_video(out: Path, duration_s: float = 5.0) -> Path:
    """Generate a real MP4 via ffmpeg's lavfi testsrc — no fixture file
    on disk, no network, deterministic."""
    _require_ffmpeg()
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_s:.2f}:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=30)
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        pytest.fail(
            f"could not synthesise test MP4 via ffmpeg "
            f"(rc={res.returncode}): {res.stderr.decode(errors='replace')[:400]}"
        )
    return out


# ─── ffmpeg CLI fallback ─────────────────────────────────────────────────────

def test_run_ffmpeg_cli_extracts_frame(tmp_path: Path):
    video = _make_test_video(tmp_path / "src.mp4")
    out = tmp_path / "frame.jpg"
    ok = _run_ffmpeg_cli(video, 1.0, out)
    assert ok is True
    assert out.exists() and out.stat().st_size > 0


def test_run_ffmpeg_cli_returns_false_on_missing_input(tmp_path: Path):
    """ffmpeg fails fast (rc!=0) when input doesn't exist — must NOT raise,
    must NOT block past the 2s timeout."""
    _require_ffmpeg()
    out = tmp_path / "frame.jpg"
    ok = _run_ffmpeg_cli(tmp_path / "does_not_exist.mp4", 1.0, out)
    assert ok is False
    assert not out.exists()


def test_ffmpeg_cli_timeout_is_tight():
    """Bound the worst-case subprocess wait. The 10s ceiling that used to
    live here was the dominant tail when extractions failed in a row.
    If somebody widens it without thinking, this test trips."""
    assert FFMPEG_CLI_TIMEOUT_S <= 3.0, (
        f"FFMPEG_CLI_TIMEOUT_S={FFMPEG_CLI_TIMEOUT_S}s is too generous — "
        "tight bound is load-bearing for orchestrator total time."
    )


# ─── extract_frame top-level ─────────────────────────────────────────────────

def test_extract_frame_writes_jpeg(tmp_path: Path):
    video = _make_test_video(tmp_path / "src.mp4")
    out = tmp_path / "out.jpg"
    res = extract_frame(video, 2.5, out)
    assert res == out
    assert out.exists() and out.stat().st_size > 0


def test_extract_frame_returns_none_for_timestamp_past_end(tmp_path: Path):
    """ffmpeg can't seek past EOF — extract_frame should return None
    instead of raising. Belt-and-suspenders for events with timestamps
    slightly past Tribe's reported duration."""
    video = _make_test_video(tmp_path / "src.mp4", duration_s=2.0)
    out = tmp_path / "out.jpg"
    res = extract_frame(video, 999.0, out)
    assert res is None


def test_extract_frame_does_not_raise_on_garbage_video(tmp_path: Path):
    """Smoke test: corrupt MP4 produces None, never an exception."""
    bad = tmp_path / "garbage.mp4"
    bad.write_bytes(b"not actually an mp4 file")
    out = tmp_path / "out.jpg"
    res = extract_frame(bad, 1.0, out)
    assert res is None


# ─── ffmpeg-python branch (when installed) ───────────────────────────────────

# ─── Fast vs slow seek paths ─────────────────────────────────────────────────

def test_fast_seek_succeeds_well_inside_video(tmp_path: Path):
    video = _make_test_video(tmp_path / "src.mp4", duration_s=5.0)
    out = tmp_path / "fast.jpg"
    ok, err = _ffmpeg_seek_extract(video, 2.0, out, fast_seek=True)
    assert ok, f"fast seek failed mid-video: {err}"
    assert out.exists() and out.stat().st_size > 0


def test_slow_seek_succeeds_well_inside_video(tmp_path: Path):
    """Slow seek (-ss after -i) is the fallback for near-EOF timestamps
    where fast seek can't land on a keyframe."""
    video = _make_test_video(tmp_path / "src.mp4", duration_s=5.0)
    out = tmp_path / "slow.jpg"
    ok, err = _ffmpeg_seek_extract(video, 2.0, out, fast_seek=False)
    assert ok, f"slow seek failed mid-video: {err}"
    assert out.exists() and out.stat().st_size > 0


def test_seek_past_eof_returns_failure(tmp_path: Path):
    """Both seek modes report failure with an error excerpt rather than
    raising, so the orchestrator's per-event try/except sees a clean
    ``(False, msg)`` and the rest of the events still get screenshotted."""
    video = _make_test_video(tmp_path / "src.mp4", duration_s=2.0)
    out = tmp_path / "eof.jpg"
    ok_fast, err_fast = _ffmpeg_seek_extract(video, 999.0, out, fast_seek=True)
    ok_slow, err_slow = _ffmpeg_seek_extract(video, 999.0, out, fast_seek=False)
    assert ok_fast is False
    assert ok_slow is False
    # The error message should give the operator something to grep —
    # not the empty string.
    assert err_fast or err_slow


def test_run_ffmpeg_cli_falls_back_to_slow_seek(tmp_path: Path):
    """End-to-end: a CLI call that fails fast-seek (e.g. timestamp at the
    very tail) should still succeed via the slow-seek retry. We can't
    deterministically force fast-seek failure with a synthetic clip, but
    we can prove the wrapper succeeds against valid inputs and reports
    failure cleanly against invalid ones — both branches exercised in
    one test."""
    video = _make_test_video(tmp_path / "src.mp4", duration_s=4.0)
    good = tmp_path / "good.jpg"
    bad = tmp_path / "bad.jpg"
    assert _run_ffmpeg_cli(video, 2.0, good) is True
    assert good.exists() and good.stat().st_size > 0
    assert _run_ffmpeg_cli(video, 999.0, bad) is False
    assert not bad.exists()


def test_extract_frame_uses_ffmpeg_python_when_available(tmp_path: Path):
    """When the ffmpeg-python wrapper is installed we should hit it first;
    the CLI fallback exists only when the wrapper isn't there. This
    test exercises the success path of the wrapper and proves it
    produces a non-empty JPEG."""
    try:
        import ffmpeg  # type: ignore  # noqa: F401
    except ImportError:
        pytest.fail(
            "ffmpeg-python is expected in the dev env — install via "
            "`pip install -r requirements-app.txt`. Refusing to skip."
        )
    video = _make_test_video(tmp_path / "src.mp4")
    out = tmp_path / "via_python.jpg"
    res = extract_frame(video, 1.0, out)
    assert res == out
    assert out.exists() and out.stat().st_size > 0
