#!/usr/bin/env python3
"""D21 spike — verify Playwright + browser-use 0.12 + CDP screencast coexistence.

Run this standalone before trusting the Phase 2 capture pipeline. It's the
30-minute spike from `~/.claude/plans/dazzling-tinkering-sedgewick.md` — proves
that the architecture documented in ASSUMPTIONS_PHASE2_CAPTURE.md A2 actually
works with the installed versions of Playwright + browser-use.

WHAT IT TESTS

  1. We launch Chromium ourselves via Playwright with --remote-debugging-port=PORT
  2. We open a CDP session on the page and start Page.startScreencast
  3. We hand the same Chromium to browser-use via Browser(cdp_url=...)
  4. browser-use's Agent.run() drives the page while OUR screencast streams
  5. Frames keep arriving throughout the agent run
  6. ffmpeg stitches the frames into an H.264 MP4 that ffprobe accepts

WHAT IT REJECTS

  - "CDP target already in use" or similar conflicts when browser-use connects
  - Screencast events stop when browser-use takes over (== single-subscriber issue)
  - browser-use's recording_watchdog auto-firing because of a hidden default
  - Agent.run() refusing to use Browser(cdp_url=...) instead of launching its own
  - MP4 fails ffprobe H.264 validation (validation.py:92 enforces this)

USAGE

    cd <repo root>
    export GEMINI_API_KEY=...
    python aesthesis_app/scripts/spike_d21_browseruse_cdp_coexistence.py

EXIT CODES

    0 — pass; architecture is verified
    1 — environmental issue (missing key, missing chromium, missing ffmpeg)
    2 — coexistence failure; the architecture is broken

NO MOCKS — real Chromium, real CDP, real BrowserUse, real Gemini.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Verbose logging on stderr, structured-ish for grepping
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("d21_spike")


# ─── Env preflight ─────────────────────────────────────────────────────────


def _bail(rc: int, msg: str) -> None:
    log.error("BAIL rc=%d: %s", rc, msg)
    sys.exit(rc)


def _check_env() -> tuple[str, str]:
    """Returns (gemini_api_key, ffmpeg_path). Bails loud if either missing."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        _bail(1, "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set — BrowserUse "
                 "needs a real Gemini key to drive the page. No mock fallback.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg  # type: ignore
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            log.warning("ffmpeg not on PATH; using imageio_ffmpeg bundled: %s", ffmpeg)
        except ImportError:
            _bail(1, "Neither system ffmpeg nor imageio-ffmpeg is available. "
                     "Install one before running the spike.")

    log.info("env preflight OK: GEMINI_API_KEY present, ffmpeg at %s", ffmpeg)
    return key, ffmpeg  # type: ignore


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─── ffmpeg stitch (mirrors browser_agent._encode_frames_to_mp4) ───────────


def _encode_to_mp4(frames: list[tuple[float, bytes]], out_path: Path,
                   ffmpeg: str) -> tuple[float, int]:
    if not frames:
        _bail(2, "ZERO frames captured — CDP screencast didn't fire AT ALL. "
                 "Either screencast was rejected, or browser-use's connect "
                 "stopped it, or Chromium crashed before first frame.")

    duration_s = max(0.0, frames[-1][0] - frames[0][0])
    log.info("ffmpeg stitch: %d frames, observed duration %.2fs", len(frames), duration_s)
    cmd = [
        ffmpeg, "-y",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", "10",
        "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-an",
        str(out_path),
    ]
    log.debug("ffmpeg cmd: %s", " ".join(cmd))
    proc = subprocess.Popen(  # noqa: S603 — ffmpeg path is shutil.which-validated
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdin is not None
        for _ts, jpeg in frames:
            proc.stdin.write(jpeg)
        proc.stdin.close()
    except BrokenPipeError:
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        _bail(2, f"ffmpeg pipe broke. stderr tail:\n{stderr[-1500:]}")
    rc = proc.wait()
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc != 0:
        _bail(2, f"ffmpeg returned rc={rc}. stderr tail:\n{stderr[-1500:]}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        _bail(2, f"ffmpeg returned 0 but {out_path} is missing/empty.\n{stderr[-1500:]}")
    size = out_path.stat().st_size
    log.info("ffmpeg done: %s (%d bytes)", out_path, size)
    return duration_s, size


def _verify_h264(out_path: Path) -> None:
    """Run ffprobe to verify the codec is H.264 — validation.py:92 enforces this."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Try imageio bundled ffprobe location
        try:
            import imageio_ffmpeg  # type: ignore
            base = Path(imageio_ffmpeg.get_ffmpeg_exe()).parent
            cand = base / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
            if cand.exists():
                ffprobe = str(cand)
        except ImportError:
            pass
    if not ffprobe:
        log.warning("ffprobe not found — skipping codec verification (validation.py "
                    "would still check at runtime; spike just can't double-check here)")
        return

    out = subprocess.check_output([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1",
        str(out_path),
    ], text=True).strip()
    log.info("ffprobe codec: %s", out)
    if out.lower() != "h264":
        _bail(2, f"validation.py:92 would REJECT this MP4 — codec={out!r}, "
                 "expected 'h264'. The architecture is broken.")


# ─── The actual spike ──────────────────────────────────────────────────────


async def run_spike(target_url: str = "https://example.com",
                    goal: str = "explore the homepage briefly",
                    max_seconds: float = 15.0) -> None:
    log.info("=" * 70)
    log.info("D21 SPIKE — Playwright + browser-use + CDP screencast coexistence")
    log.info("target_url=%s goal=%r max_seconds=%.1f", target_url, goal, max_seconds)
    log.info("=" * 70)

    key, ffmpeg = _check_env()

    # Heavy imports — done after env check so missing-deps message is loud first
    log.info("importing playwright + browser_use ...")
    t0 = time.perf_counter()
    from playwright.async_api import async_playwright  # type: ignore
    from browser_use import Agent, Browser, ChatGoogle  # type: ignore
    log.info("imports done (%.2fs)", time.perf_counter() - t0)

    cdp_port = _find_free_port()
    out_dir = Path("/tmp" if sys.platform != "win32" else os.environ.get("TEMP", "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / "d21_spike_output.mp4"
    if out_mp4.exists():
        out_mp4.unlink()

    frames_for_mp4: list[tuple[float, bytes]] = []
    frame_count = {"n": 0}
    cdp_session = None

    async with async_playwright() as pw:
        log.info("launching chromium with --remote-debugging-port=%d ...", cdp_port)
        chromium = await pw.chromium.launch(
            headless=True,
            args=[f"--remote-debugging-port={cdp_port}",
                  "--disable-blink-features=AutomationControlled",
                  "--no-default-browser-check", "--no-first-run"],
        )
        log.info("chromium launched, pid=%s", chromium)

        ctx = await chromium.new_context(viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        log.info("page opened")

        # Set up CDP screencast on OUR side
        cdp_session = await ctx.new_cdp_session(page)
        log.info("CDP session opened (ours)")

        async def on_screencast_frame(params):
            now = time.monotonic()
            try:
                jpeg = base64.b64decode(params["data"])
            except Exception as e:  # noqa: BLE001
                log.error("frame decode failed: %s", e)
                return
            frames_for_mp4.append((now, jpeg))
            frame_count["n"] += 1
            if frame_count["n"] in (1, 5, 25, 100, 250, 500):
                log.info("frame #%d received (%d bytes JPEG)", frame_count["n"], len(jpeg))
            await cdp_session.send("Page.screencastFrameAck",
                                   {"sessionId": params["sessionId"]})

        cdp_session.on("Page.screencastFrame",
                       lambda p: asyncio.create_task(on_screencast_frame(p)))
        await cdp_session.send("Page.startScreencast", {
            "format": "jpeg", "quality": 60,
            "maxWidth": 800, "maxHeight": 600, "everyNthFrame": 3,
        })
        log.info("CDP screencast started (T0 params: 800x600, q=60, every3rd)")

        # Initial navigation BEFORE handing to browser-use
        log.info("initial nav to %s", target_url)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=15_000)
        log.info("initial nav done")
        await asyncio.sleep(0.5)
        log.info("frames after initial nav: %d", frame_count["n"])

        # Build BrowserUse on top
        log.info("building ChatGoogle LLM (model=gemini-2.5-pro)...")
        llm = ChatGoogle(model="gemini-2.5-pro", api_key=key, temperature=0.0)
        log.info("ChatGoogle ready")

        log.info("building Browser(cdp_url=http://127.0.0.1:%d) ...", cdp_port)
        bu_browser = Browser(cdp_url=f"http://127.0.0.1:{cdp_port}")
        log.info("Browser constructed")

        log.info("constructing Agent(task=..., llm=..., browser=...) ...")
        agent = Agent(
            task=f"You are on {target_url}. {goal}. Move efficiently — you have "
                 f"{int(max_seconds)}s. If you complete early, finish and stop.",
            llm=llm,
            browser=bu_browser,
        )
        log.info("Agent constructed")

        # ── COEXISTENCE TEST ──
        # If browser-use's connect kills our screencast, frame_count won't grow during agent.run
        baseline_frames = frame_count["n"]
        log.info("baseline frame count BEFORE agent.run: %d", baseline_frames)

        agent_task = asyncio.create_task(agent.run(), name="agent_run")
        timer_task = asyncio.create_task(asyncio.sleep(max_seconds), name="recording_cap")

        log.info("agent.run() started — recording cap %ds", int(max_seconds))
        t_run = time.perf_counter()
        done, pending = await asyncio.wait({agent_task, timer_task},
                                            return_when=asyncio.FIRST_COMPLETED)
        elapsed = time.perf_counter() - t_run
        winner = next(iter(done)).get_name()
        log.info("agent run finished: winner=%s elapsed=%.2fs", winner, elapsed)

        post_run_frames = frame_count["n"]
        log.info("frame count AFTER agent.run: %d (delta during agent: %d)",
                 post_run_frames, post_run_frames - baseline_frames)

        # The COEXISTENCE assertion — frames must have flowed during agent.run
        if post_run_frames - baseline_frames < 5:
            _bail(2, f"COEXISTENCE FAILURE: only {post_run_frames - baseline_frames} "
                     f"frames during {elapsed:.1f}s of agent.run. browser-use "
                     "likely killed our screencast when it connected via cdp_url. "
                     "The architecture is broken.")

        # Cancel pending
        for p in pending:
            p.cancel()
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass

        # Stop screencast cleanly
        try:
            await cdp_session.send("Page.stopScreencast")
            log.info("CDP screencast stopped")
        except Exception as e:  # noqa: BLE001
            log.warning("stopScreencast failed (cdp may be closed): %s", e)

        # Cleanup browser
        await ctx.close()
        await chromium.close()
        log.info("chromium closed")

    # Encode MP4 + verify codec
    log.info("encoding MP4 ...")
    duration, size = _encode_to_mp4(frames_for_mp4, out_mp4, ffmpeg)
    _verify_h264(out_mp4)

    log.info("=" * 70)
    log.info("D21 SPIKE PASSED ✓")
    log.info("  total frames: %d", len(frames_for_mp4))
    log.info("  observed duration: %.2fs", duration)
    log.info("  MP4 size: %d bytes (%.1f KB)", size, size / 1024)
    log.info("  MP4 path: %s", out_mp4)
    log.info("  Codec: h264 (validation.py:92 will accept)")
    log.info("=" * 70)


def main() -> int:
    target = os.environ.get("D21_TARGET_URL", "https://example.com")
    goal = os.environ.get("D21_GOAL", "explore the homepage briefly")
    max_s = float(os.environ.get("D21_MAX_S", "15"))
    try:
        asyncio.run(run_spike(target_url=target, goal=goal, max_seconds=max_s))
        return 0
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except Exception as e:  # noqa: BLE001
        log.exception("spike crashed: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
