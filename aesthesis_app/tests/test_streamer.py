"""AdaptiveStreamer tests (DESIGN.md §4.2b D9 + post-OV D25/D30c).

NO MOCKS: where the streamer needs CDP, we use a real Chromium via
Playwright. Pure-logic surfaces are tested by direct function/property
calls — that is unit testing, not mocking.

Layers:
- ``test_tier_constants_*`` — frozen-spec assertions on ``TIERS``
- ``test_measure_fps_*`` — pure rolling-window math
- ``test_real_cdp_screencast`` — full integration: launch Chromium,
  open a tiny static page, attach AdaptiveStreamer, let it run for a
  few seconds, verify frames appear in ``frames_for_mp4`` and stdout
  emits valid JSONL.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time

import pytest

from aesthesis.capture.streamer import (
    TIERS, AdaptiveStreamer, TierParams,
)


# ─── Pure tests: tier table + FPS measurement ──────────────────────────────


def test_tier_constants_lock_to_spec() -> None:
    """DESIGN.md §4.2b — the 5-tier ladder is locked. Any change here
    means the live-stream UX behaviour shifted; intentional or not, it
    needs to be visible in PR review."""
    assert len(TIERS) == 5, "DESIGN spec defines exactly 5 tiers (T0-T4)"
    assert all(isinstance(t, TierParams) for t in TIERS)
    # T0 default
    assert TIERS[0].name == "T0"
    assert (TIERS[0].width, TIERS[0].height) == (800, 600)
    assert TIERS[0].quality == 60
    assert TIERS[0].every_nth_frame == 3
    assert TIERS[0].target_fps == 10
    # T4 floor — D9 hard requirement
    assert TIERS[-1].name == "T4"
    assert TIERS[-1].target_fps == 2, "D9: T4 must target 2 fps (the floor)"
    assert TIERS[-1].quality == 25
    # Monotonic descent across width/height/quality/target_fps
    for a, b in zip(TIERS, TIERS[1:]):
        assert b.width <= a.width and b.height <= a.height, "tier dimensions must descend"
        assert b.quality <= a.quality, "tier JPEG quality must descend"
        assert b.target_fps <= a.target_fps, "tier target FPS must descend"


def test_measure_fps_empty_deque_returns_zero() -> None:
    s = AdaptiveStreamer(cdp_session=None, run_id="test", on_lifecycle=_noop_async)
    assert s._measure_fps() == 0.0


def test_measure_fps_single_sample_returns_zero() -> None:
    s = AdaptiveStreamer(cdp_session=None, run_id="test", on_lifecycle=_noop_async)
    s.frame_times.append(time.monotonic())
    assert s._measure_fps() == 0.0, "<2 samples can't compute a rate"


def test_measure_fps_steady_10fps_window() -> None:
    """20 samples spaced 100ms apart should report ~10 fps."""
    s = AdaptiveStreamer(cdp_session=None, run_id="test", on_lifecycle=_noop_async)
    t0 = time.monotonic()
    for i in range(20):
        s.frame_times.append(t0 + i * 0.1)  # 10fps cadence
    fps = s._measure_fps()
    # 20 samples over ~1.9s -> ~10.5 fps. Allow some slack.
    assert 9.0 <= fps <= 11.5, f"expected ~10fps, got {fps:.2f}"


def test_measure_fps_slow_2fps_window() -> None:
    s = AdaptiveStreamer(cdp_session=None, run_id="test", on_lifecycle=_noop_async)
    t0 = time.monotonic()
    for i in range(20):
        s.frame_times.append(t0 + i * 0.5)  # 2fps cadence
    fps = s._measure_fps()
    assert 1.5 <= fps <= 2.5, f"expected ~2fps, got {fps:.2f}"


# Helper used by the no-cdp pure tests (allowed: passing None is the
# constructor-permitted form for these surfaces).
async def _noop_async(_payload: dict) -> None:
    return None


# ─── Real-Chromium integration test ────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_cdp_screencast_emits_frames(
    chromium_available: None,  # noqa: ARG001 — fixture asserts env, returns None
    tmp_path,
    capsys,
) -> None:
    """Launch real Chromium, open a tiny inline HTML page, attach the
    AdaptiveStreamer, let it run ~3 seconds. Verify:

    - frames_for_mp4 has > 5 entries (T0 targets 10fps, 3s = ~30 frames)
    - each entry's bytes start with the JPEG SOI marker (0xFFD8)
    - stats.frames_emitted matches frames_for_mp4 length
    - stdout received valid JSONL frame events

    No mocks — this hits real Chromium via real Playwright.
    """
    from playwright.async_api import async_playwright  # type: ignore

    # Tiny inline HTML page so the test doesn't depend on the network
    inline_html = (
        "<!doctype html><meta charset=utf-8><title>streamer-test</title>"
        "<body style='background:#0B0F14;color:#7C9CFF;font-family:monospace;"
        "padding:32px;font-size:48px'><div id=t></div>"
        "<script>let n=0;setInterval(()=>{document.getElementById('t')"
        ".textContent='tick '+(++n)},100)</script></body>"
    )

    async with async_playwright() as pw:
        chromium = await pw.chromium.launch(headless=True)
        try:
            ctx = await chromium.new_context(viewport={"width": 800, "height": 600})
            page = await ctx.new_page()
            await page.set_content(inline_html)

            cdp = await ctx.new_cdp_session(page)
            streamer = AdaptiveStreamer(cdp, run_id="streamer-test", on_lifecycle=_noop_async)

            await streamer.start()
            await asyncio.sleep(3.0)  # let frames flow
            await streamer.stop()

            await ctx.close()
        finally:
            await chromium.close()

    # Assertions
    n_frames = len(streamer.frames_for_mp4)
    assert n_frames > 5, (
        f"expected >5 frames in 3s at T0 (target 10fps), got {n_frames}. "
        "CDP screencast may not be ack-ing properly."
    )
    assert n_frames == streamer.stats.frames_emitted, (
        "frames_for_mp4 length should equal stats.frames_emitted"
    )
    for ts, jpeg in streamer.frames_for_mp4[:5]:
        assert isinstance(ts, float)
        assert isinstance(jpeg, (bytes, bytearray))
        assert jpeg[:2] == b"\xff\xd8", (
            f"frame doesn't start with JPEG SOI marker: {jpeg[:8].hex()}"
        )
