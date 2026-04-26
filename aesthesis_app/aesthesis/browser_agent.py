"""Browser-agent subprocess entry — `python -m aesthesis.browser_agent`.

The load-bearing one-shot binary that ``capture.runner.CaptureRunner``
spawns per capture run. Two-phase lifecycle:

PHASE 1 — pre-warm (fires immediately on spawn):

  1. Launch Playwright Chromium with ``--remote-debugging-port=PORT``
     so browser-use can attach via ``Browser(cdp_url=...)`` (the only
     coexistence pattern that lets us record AND drive the same tab;
     see ASSUMPTIONS_PHASE2_CAPTURE.md A2).
  2. Open a stand-by page (data: URL with a friendly "ready" message).
  3. Open CDP session, instantiate ``capture.streamer.AdaptiveStreamer``,
     start screencast — frames begin flowing to stdout immediately so
     the frontend can render the live panel.
  4. Construct ``ChatGoogle`` LLM (validates GEMINI_API_KEY, primes
     the genai HTTPS connection).
  5. Emit ``{"type": "prewarm_ready", "run_id": ..., "cdp_port": ...}``
     on stdout.
  6. Wait for stdin: a single JSON line specifying the actual capture
     parameters (``url``, ``goal``, optional ``cookies``).

PHASE 2 — capture (fires when stdin start command arrives):

  7. Inject cookies (D31 auth) onto the existing context.
  8. Navigate the page to the target URL.
  9. Construct ``Agent(task, llm, browser=Browser(cdp_url=...))``.
 10. Race ``agent.run()`` against ``capture_recording_cap_s`` (D7).
 11. Stop screencast; ffmpeg-stitch JPEG frames into H.264 MP4
     (validation.py:92 hard-requires h264; see A3).
 12. Write BrowserUse action history to ``actions.jsonl`` (D15).
 13. Emit ``capture_complete`` JSONL, exit 0.

Sad path: at any point, emit ``capture_failed`` with a typed reason
(``timeout``, ``crashed``, ``navigation_error``, ``setup_error``) and
exit 1. Never swallow — tests fail loudly per project memory
``feedback_no_mocks``.

Stdout = protocol (the parent reads JSONL line-by-line). Stderr =
human/log output (parent forwards to backend log with run_id
correlation).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .capture.protocol import AuthSpec
from .capture.streamer import AdaptiveStreamer
from .logging_config import configure_logging

log = logging.getLogger("aesthesis.browser_agent")


# Stand-by HTML shown before the user clicks Start. data: URL keeps it
# self-contained; no network fetch needed.
_STANDBY_HTML = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>Aesthesis — ready</title>
<style>
  html,body{margin:0;height:100%;background:#0B0F14;color:#7C9CFF;
    font-family:ui-sans-serif,system-ui,sans-serif;display:flex;
    align-items:center;justify-content:center}
  .wrap{text-align:center;padding:32px}
  .pulse{width:14px;height:14px;border-radius:50%;background:#5CF2C5;
    margin:0 auto 16px;animation:p 1.4s ease-in-out infinite}
  h1{font-weight:300;font-size:28px;margin:0 0 8px;color:#e8eaf0;
    letter-spacing:-0.01em}
  p{color:rgba(255,255,255,0.45);font-size:14px;margin:0}
  @keyframes p{0%,100%{opacity:.4;transform:scale(.95)}50%{opacity:1;
    transform:scale(1.1)}}
</style></head>
<body><div class="wrap"><div class="pulse"></div>
<h1>Browser ready</h1><p>Configure capture and click Start.</p>
</div></body></html>
"""
_STANDBY_DATA_URL = (
    "data:text/html;charset=utf-8,"
    + _STANDBY_HTML.replace("\n", "").replace(" ", "%20")
)


# ─── Argv & logging setup ──────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aesthesis.browser_agent",
        description=(
            "Two-phase capture subprocess: pre-warm Chromium + CDP screencast "
            "+ ChatGoogle, wait for stdin start command, then drive a URL "
            "with BrowserUse and produce an H.264 MP4."
        ),
    )
    p.add_argument("--run-dir", required=True, type=Path,
                   help="working dir for video.mp4 + actions.jsonl")
    p.add_argument("--run-id", required=True,
                   help="opaque ID for log correlation; matches parent's run_id")
    p.add_argument("--max-recording-s", type=float, default=30.0,
                   help="hard inner cap on the screencast portion (D7)")
    p.add_argument("--headless", type=int, default=1, choices=(0, 1),
                   help="chromium headless mode")
    p.add_argument("--browseruse-model", default="gemini-2.5-pro",
                   help="Gemini model name for the BrowserUse Agent LLM")
    p.add_argument("--viewport-width", type=int, default=1280)
    p.add_argument("--viewport-height", type=int, default=720)
    return p.parse_args()


# ─── Stdout protocol helpers ───────────────────────────────────────────────


def _emit_stdout(payload: dict) -> None:
    """Write one JSONL line to stdout. The parent reads line-by-line."""
    line = json.dumps(payload, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


async def _emit_stdout_async(payload: dict) -> None:
    """Async wrapper for callbacks that expect ``Awaitable[None]``."""
    _emit_stdout(payload)


def _emit_failed(run_id: str, reason: str, message: str) -> None:
    """Always called via the top-level except. Never raises."""
    log.error(
        "browser_agent.capture_failed",
        extra={
            "step": "agent", "run_id": run_id,
            "reason": reason, "message": message,
        },
    )
    _emit_stdout({
        "type": "capture_failed",
        "run_id": run_id,
        "reason": reason,
        "message": message,
    })


# ─── Stdin reader (parent → subprocess command channel) ────────────────────
#
# asyncio support for reading the subprocess's own stdin is platform-specific
# (Windows ProactorEventLoop has a long history of issues with stdin pipes).
# We side-step that with a daemon thread that does blocking line reads on
# sys.stdin and pushes complete lines onto an asyncio.Queue via
# loop.call_soon_threadsafe — works the same everywhere.


def _start_stdin_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> threading.Thread:
    """Start a daemon thread that pumps sys.stdin lines onto ``queue``."""

    def _thread_main() -> None:
        log.debug("stdin_reader: thread started")
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                log.debug("stdin_reader: line received (%d chars)", len(line))
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception as e:  # noqa: BLE001 — thread death must not crash main
            log.warning("stdin_reader: thread crashed: %s", e)
        finally:
            # Sentinel so the awaiter knows stdin is closed
            loop.call_soon_threadsafe(queue.put_nowait, None)
            log.debug("stdin_reader: thread exiting")

    t = threading.Thread(target=_thread_main, daemon=True, name="stdin_reader")
    t.start()
    return t


# ─── ffmpeg discovery + stitch ─────────────────────────────────────────────


def _find_ffmpeg() -> str:
    """Return the absolute path to ffmpeg, or raise loudly.

    ASSUMPTION (see ASSUMPTIONS_PHASE2_CAPTURE.md A4): ffmpeg must be on PATH
    or available via the ``imageio_ffmpeg`` bundled binary. If neither, the
    build fails. No silent fallback to "skip MP4 encoding."
    """
    found = shutil.which("ffmpeg")
    if found:
        log.debug("ffmpeg.found", extra={"path": found, "via": "PATH"})
        return found

    try:
        import imageio_ffmpeg  # type: ignore
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        log.warning(
            "ffmpeg.found_via_bundled (PATH lookup failed; using imageio-ffmpeg)",
            extra={"path": bundled},
        )
        return bundled
    except ImportError:
        pass

    raise RuntimeError(
        "ffmpeg binary not found on PATH and imageio-ffmpeg not installed. "
        "Install ffmpeg system-wide or `pip install imageio-ffmpeg`. "
        "Capture pipeline cannot finalise the H.264 MP4 without it."
    )


def _encode_frames_to_mp4(
    frames: list[tuple[float, bytes]],
    out_path: Path,
    *,
    run_id: str,
) -> tuple[float, int]:
    """Stitch JPEG bytes into an H.264 MP4 via ffmpeg image2pipe.

    Returns ``(observed_duration_s, mp4_size_bytes)``. Raises loudly if
    ffmpeg is missing or returns non-zero (tests must fail, per project
    memory).
    """
    if not frames:
        raise RuntimeError(
            "no frames captured — CDP screencast produced zero output. "
            "Likely Chromium crashed before first frame, or screencast "
            "params were rejected."
        )

    ffmpeg = _find_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration_s = max(0.0, frames[-1][0] - frames[0][0])
    log.info(
        "ffmpeg.encode_begin",
        extra={
            "step": "encode", "run_id": run_id,
            "n_frames": len(frames), "duration_s": round(duration_s, 2),
            "out_path": str(out_path),
        },
    )

    cmd = [
        ffmpeg, "-y",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-framerate", "10",
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",  # video-only (DESIGN.md §17 audio-strip-end-to-end)
        str(out_path),
    ]
    log.debug("ffmpeg.cmd", extra={"step": "encode", "run_id": run_id,
                                    "cmd": " ".join(cmd)})

    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603 — ffmpeg path is shutil.which-validated
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    try:
        assert proc.stdin is not None
        for _ts, jpeg_bytes in frames:
            proc.stdin.write(jpeg_bytes)
        proc.stdin.close()
    except BrokenPipeError:
        stderr_tail = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise RuntimeError(
            f"ffmpeg pipe broke during frame feed. stderr tail:\n{stderr_tail[-2000:]}"
        )

    rc = proc.wait()
    encode_ms = (time.perf_counter() - t0) * 1000.0
    stderr_text = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""

    if rc != 0:
        raise RuntimeError(
            f"ffmpeg exited rc={rc} (out_path={out_path}). "
            f"stderr tail:\n{stderr_text[-2000:]}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(
            f"ffmpeg returned 0 but output file is missing/empty at {out_path}. "
            f"stderr tail:\n{stderr_text[-2000:]}"
        )

    size = out_path.stat().st_size
    log.info(
        "ffmpeg.encode_done",
        extra={
            "step": "encode", "run_id": run_id,
            "elapsed_ms": round(encode_ms, 2),
            "out_size_bytes": size,
            "duration_s": round(duration_s, 2),
        },
    )
    return duration_s, size


# ─── Free-port helper (CDP --remote-debugging-port=PORT) ──────────────────


def _find_free_port() -> int:
    """Bind a socket to port 0, read the assigned port, close.

    There's a tiny TOCTOU race between us closing and Chromium binding,
    but with D19 (single concurrent capture), the only competitor is
    background OS chatter. Acceptable.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port


# ─── BrowserUse setup ──────────────────────────────────────────────────────


GOAL_PROMPT_TEMPLATE = """\
You are a first-time visitor to {url}. Your task: {goal}.

Constraints:
- You have {max_seconds} seconds wall-clock total. Move efficiently.
- Behave like a curious-but-impatient real human. Read the hero, click
  the obvious CTA, fill 1-2 fields if a form appears.
- Do NOT refresh, open dev tools, type sensitive info, or open new tabs.
- If you complete the goal early, that's fine — finish and stop.
- If you hit an unexpected page (404, login wall), stop immediately.
"""


def _build_task_prompt(url: str, goal: str | None, max_seconds: float) -> str:
    """Produce the BrowserUse task string per DESIGN.md §4.1."""
    return GOAL_PROMPT_TEMPLATE.format(
        url=url,
        goal=goal or "form a first impression of the product as a real visitor would",
        max_seconds=int(max_seconds),
    )


def _build_llm(model_name: str, *, run_id: str):  # noqa: ANN202 — type from browser_use lazy-import
    """Construct browser-use's native ``ChatGoogle`` LLM wrapper.

    browser-use 0.12.x ships its own LLM abstractions — we do NOT use
    langchain. ``ChatGoogle`` wraps Google's official ``genai`` client
    directly and accepts ``api_key`` as a plain string.
    """
    from browser_use import ChatGoogle  # type: ignore

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set — BrowserUse "
            "needs a Gemini key to drive the page. Set it in .env or "
            "the subprocess environment."
        )

    log.info(
        "llm.build",
        extra={"step": "llm", "run_id": run_id, "model": model_name,
               "key_source": "GEMINI_API_KEY" if os.environ.get("GEMINI_API_KEY") else "GOOGLE_API_KEY"},
    )
    return ChatGoogle(
        model=model_name,
        api_key=api_key,
        temperature=0.0,
    )


# ─── Action history extraction ─────────────────────────────────────────────


def _serialise_action_history(agent: Any, *, run_id: str) -> list[dict]:
    """Pull a JSON-friendly list of (timestamp, description) actions from
    ``agent.history``.

    Per browser_use/agent/service.py:435, ``Agent.__init__`` always sets
    ``self.history = AgentHistoryList(history=[], usage=None)``. Use the
    canonical ``.agent_steps()`` view first, fall back to ``.history.history``,
    dump each item via Pydantic ``model_dump()``.
    """
    history_list = getattr(agent, "history", None)
    if history_list is None:
        log.warning(
            "agent.history_not_found",
            extra={"step": "agent", "run_id": run_id,
                   "agent_attrs": [a for a in dir(agent) if not a.startswith("_")][:25]},
        )
        return []

    steps = None
    if hasattr(history_list, "agent_steps") and callable(history_list.agent_steps):
        try:
            steps = history_list.agent_steps()
        except Exception as e:  # noqa: BLE001
            log.debug("agent.history.agent_steps_failed: %s", e,
                      extra={"step": "agent", "run_id": run_id})
            steps = None

    if steps is None:
        steps = getattr(history_list, "history", None)

    if not steps:
        log.info(
            "agent.history_empty",
            extra={"step": "agent", "run_id": run_id,
                   "history_type": type(history_list).__name__},
        )
        return []

    log.info(
        "agent.history_extracted",
        extra={"step": "agent", "run_id": run_id,
               "n_entries": len(steps),
               "history_type": type(history_list).__name__},
    )

    out: list[dict] = []
    for i, step in enumerate(steps):
        if hasattr(step, "model_dump"):
            try:
                dumped = step.model_dump()
                desc = str(dumped.get("result") or dumped.get("model_output") or dumped)
            except Exception:  # noqa: BLE001
                desc = str(step)
        else:
            desc = str(step)
        out.append({
            "i": i,
            "timestamp_s": float(i),
            "description": desc[:500],
        })
    return out


# ─── Start command parsing (stdin) ─────────────────────────────────────────


class StartCommand:
    """Parsed payload of the stdin start command."""

    def __init__(self, url: str, goal: str | None, auth: AuthSpec | None):
        self.url = url
        self.goal = goal
        self.auth = auth

    @classmethod
    def from_json_line(cls, line: str) -> "StartCommand":
        payload = json.loads(line)
        if payload.get("type") != "start":
            raise ValueError(
                f"expected stdin command type='start', got type={payload.get('type')!r}"
            )
        url = payload.get("url")
        if not url or not isinstance(url, str):
            raise ValueError(f"start command missing 'url' string field: {payload}")
        goal = payload.get("goal")
        auth_dict = payload.get("auth")
        auth = AuthSpec(**auth_dict) if auth_dict else None
        return cls(url=url, goal=goal, auth=auth)


# ─── Main async runner — two-phase ────────────────────────────────────────


async def _run_two_phase(args: argparse.Namespace) -> None:
    """Phase 1: pre-warm. Phase 2: capture (gated on stdin)."""
    run_id = args.run_id
    run_dir: Path = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    cdp_port = _find_free_port()

    log.info(
        "agent.boot",
        extra={
            "step": "agent", "run_id": run_id,
            "phase": "boot",
            "max_recording_s": args.max_recording_s,
            "headless": bool(args.headless),
            "browseruse_model": args.browseruse_model,
            "cdp_port": cdp_port,
            "viewport": [args.viewport_width, args.viewport_height],
        },
    )

    from playwright.async_api import async_playwright  # type: ignore
    from browser_use import Agent, Browser  # type: ignore

    streamer: AdaptiveStreamer | None = None
    pw_ctx = None
    chromium = None

    # Stdin queue + reader thread (cross-platform; see _start_stdin_reader docs)
    loop = asyncio.get_running_loop()
    stdin_queue: asyncio.Queue = asyncio.Queue()
    _start_stdin_reader(loop, stdin_queue)

    try:
        async with async_playwright() as pw:
            log.info("agent.playwright_launched",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming"})
            chromium = await pw.chromium.launch(
                headless=bool(args.headless),
                args=[
                    f"--remote-debugging-port={cdp_port}",
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                    "--no-first-run",
                ],
            )
            log.info("agent.chromium_launched",
                     extra={"step": "agent", "run_id": run_id,
                            "phase": "warming", "cdp_port": cdp_port})

            pw_ctx = await chromium.new_context(
                viewport={"width": args.viewport_width,
                          "height": args.viewport_height},
                ignore_https_errors=False,
            )

            page = await pw_ctx.new_page()
            log.info("agent.page_opened",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming"})

            # Open CDP session for the screencast. browser-use will get its
            # own session via cdp_url — both can coexist on the same page.
            cdp_session = await pw_ctx.new_cdp_session(page)
            log.info("agent.cdp_session_opened",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming"})

            streamer = AdaptiveStreamer(
                cdp_session, run_id=run_id,
                on_lifecycle=_emit_stdout_async,
            )
            await streamer.start()

            # Navigate to stand-by page so the user has SOMETHING to look at
            # while filling the form. data: URL = no network round-trip.
            log.info("agent.standby_nav_begin",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming"})
            await page.goto(_STANDBY_DATA_URL, wait_until="domcontentloaded",
                            timeout=5_000)
            log.info("agent.standby_nav_done",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming"})

            # Pre-build the LLM (validates API key, primes genai client connection)
            llm = _build_llm(args.browseruse_model, run_id=run_id)

            # ── Pre-warm complete — signal readiness ──
            _emit_stdout({
                "type": "prewarm_ready",
                "run_id": run_id,
                "cdp_port": cdp_port,
            })
            log.info("agent.prewarm_ready_emitted",
                     extra={"step": "agent", "run_id": run_id, "phase": "ready",
                            "cdp_port": cdp_port})

            # ── PHASE 2: wait for start command from stdin ──
            log.info("agent.awaiting_start_command",
                     extra={"step": "agent", "run_id": run_id, "phase": "ready"})

            line = await stdin_queue.get()
            if line is None:
                # stdin closed without a start command — parent dropped us
                # before the user clicked Start. Exit cleanly.
                log.warning("agent.stdin_closed_before_start",
                            extra={"step": "agent", "run_id": run_id, "phase": "ready"})
                _emit_failed(run_id, "crashed",
                             "stdin closed before start command — parent likely cancelled pre-warm")
                return

            try:
                cmd = StartCommand.from_json_line(line)
            except (json.JSONDecodeError, ValueError) as e:
                _emit_failed(run_id, "setup_error",
                             f"failed to parse start command from stdin: {e}; line={line[:200]!r}")
                return
            log.info("agent.start_command_received",
                     extra={"step": "agent", "run_id": run_id, "phase": "warming",
                            "url": cmd.url, "goal_present": cmd.goal is not None,
                            "n_cookies": len(cmd.auth.cookies) if (cmd.auth and cmd.auth.cookies) else 0})

            # Inject cookies BEFORE navigating to the real URL (D31, A12)
            if cmd.auth and cmd.auth.cookies:
                cookie_dicts = [
                    {k: v for k, v in c.model_dump().items() if v is not None}
                    for c in cmd.auth.cookies
                ]
                await pw_ctx.add_cookies(cookie_dicts)
                log.info("agent.cookies_injected",
                         extra={"step": "agent", "run_id": run_id, "phase": "warming",
                                "n_cookies": len(cookie_dicts)})

            # ── A23: drop warm-up frames from the MP4 buffer ──
            # The streamer started during pre-warm and has been stashing
            # standby-page frames into frames_for_mp4 the whole time. If
            # we don't clear them now, the final MP4 will include the
            # "Browser ready" standby HTML + the user's typing-time idle
            # + the navigation transition, contaminating the TRIBE +
            # Gemini analysis with frames that aren't part of the demo.
            #
            # Clear right BEFORE page.goto so the MP4 starts from the
            # first frame of the real URL load. The live stream
            # (sys.stdout) is unaffected — it keeps emitting every frame
            # so the user sees an uninterrupted "watch the agent work"
            # UX. Only the MP4 encoding source resets.
            n_warm_frames = len(streamer.frames_for_mp4)
            streamer.frames_for_mp4.clear()
            log.info(
                "agent.warm_frames_dropped_from_mp4",
                extra={"step": "agent", "run_id": run_id, "phase": "running",
                       "n_dropped": n_warm_frames,
                       "reason": "pre-warm standby frames not part of demo"},
            )

            # Real navigation
            log.info("agent.real_nav_begin",
                     extra={"step": "agent", "run_id": run_id, "phase": "running",
                            "url": cmd.url})
            await page.goto(cmd.url, wait_until="domcontentloaded", timeout=15_000)
            log.info("agent.real_nav_done",
                     extra={"step": "agent", "run_id": run_id, "phase": "running",
                            "n_frames_post_nav": len(streamer.frames_for_mp4)})

            # Build BrowserUse on top of OUR Chromium
            bu_browser = Browser(cdp_url=f"http://127.0.0.1:{cdp_port}")
            log.info("agent.browseruse_browser_built",
                     extra={"step": "agent", "run_id": run_id, "phase": "running",
                            "cdp_url": f"http://127.0.0.1:{cdp_port}"})

            # D26 / Task #8 guard — defense against future browser-use default
            # change that would auto-fire recording_watchdog and conflict with
            # our screencast. browser-use 0.12.6 default is None; this asserts
            # it stays None.
            _record_video_dir = getattr(bu_browser.browser_profile, "record_video_dir", None)
            if _record_video_dir is not None:
                _emit_failed(run_id, "setup_error",
                             f"browser-use's BrowserSession.browser_profile.record_video_dir "
                             f"defaulted to {_record_video_dir!r} — that would auto-fire the "
                             f"recording_watchdog and conflict with our CDP screencast. "
                             f"Aesthesis owns the screencast; pin browser-use to a version "
                             f"where this defaults to None.")
                return

            task_prompt = _build_task_prompt(cmd.url, cmd.goal, args.max_recording_s)
            log.debug("agent.task_prompt",
                      extra={"step": "agent", "run_id": run_id, "phase": "running",
                             "prompt_len": len(task_prompt)})
            agent = Agent(task=task_prompt, llm=llm, browser=bu_browser)
            log.info("agent.constructed",
                     extra={"step": "agent", "run_id": run_id, "phase": "running"})

            # Race: agent.run() vs recording cap (D7 inner timeout)
            log.info("agent.run_begin",
                     extra={"step": "agent", "run_id": run_id, "phase": "running",
                            "max_recording_s": args.max_recording_s})
            agent_task = asyncio.create_task(agent.run(), name="browseruse_agent")
            timer_task = asyncio.create_task(asyncio.sleep(args.max_recording_s),
                                             name="recording_cap_timer")
            t_run = time.perf_counter()

            done, pending = await asyncio.wait(
                {agent_task, timer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            run_elapsed = time.perf_counter() - t_run
            winner_name = next(iter(done)).get_name()
            log.info(
                "agent.run_winner",
                extra={"step": "agent", "run_id": run_id, "phase": "running",
                       "winner": winner_name, "elapsed_s": round(run_elapsed, 2)},
            )

            for p in pending:
                p.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await p

            # Stop screencast — frames_for_mp4 is now final
            await streamer.stop()

            # Pull action history (D15)
            action_log = _serialise_action_history(agent, run_id=run_id)
            actions_path = run_dir / "actions.jsonl"
            actions_path.write_text(
                "\n".join(json.dumps(a, separators=(",", ":")) for a in action_log) + "\n",
                encoding="utf-8",
            )
            log.info("agent.actions_written",
                     extra={"step": "agent", "run_id": run_id, "phase": "finalizing",
                            "path": str(actions_path), "n_actions": len(action_log)})

            # Encode MP4 (validation.py:92 enforces H.264, see A3)
            mp4_path = run_dir / "video.mp4"
            duration_s, mp4_size = _encode_frames_to_mp4(
                streamer.frames_for_mp4, mp4_path, run_id=run_id,
            )

            # Cleanup chromium
            await pw_ctx.close()
            await chromium.close()
            log.info("agent.chromium_closed",
                     extra={"step": "agent", "run_id": run_id, "phase": "finalizing"})

            _emit_stdout({
                "type": "capture_complete",
                "run_id": run_id,
                "duration_s": round(duration_s, 2),
                "mp4_size_bytes": mp4_size,
                "n_actions": len(action_log),
            })
            log.info("agent.capture_complete_emitted",
                     extra={"step": "agent", "run_id": run_id, "phase": "completed"})

    except asyncio.CancelledError:
        log.warning("agent.cancelled (likely parent SIGKILL grace)",
                    extra={"step": "agent", "run_id": run_id})
        raise


def _classify_exception_reason(exc: BaseException) -> str:
    """Map an exception to a capture_failed.reason value."""
    msg = str(exc)
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in msg.lower():
        return "navigation_error"
    if "404" in msg or "ERR_NAME_NOT_RESOLVED" in msg or "ERR_CONNECTION_REFUSED" in msg:
        return "navigation_error"
    if isinstance(exc, RuntimeError) and "ffmpeg" in msg.lower():
        return "setup_error"
    if "GEMINI_API_KEY" in msg or "API key" in msg or "credential" in msg.lower():
        return "setup_error"
    return "crashed"


def main() -> int:
    configure_logging()
    args = _parse_args()
    log.info(
        "browser_agent.main_entry",
        extra={"step": "agent", "run_id": args.run_id, "argv": sys.argv[1:]},
    )

    try:
        asyncio.run(_run_two_phase(args))
        log.info("browser_agent.main_exit_ok", extra={"run_id": args.run_id})
        return 0
    except KeyboardInterrupt:
        _emit_failed(args.run_id, "crashed", "interrupted")
        return 130
    except Exception as e:  # noqa: BLE001 — top-level boundary
        tb = traceback.format_exc()
        log.error(
            "browser_agent.main_exception",
            extra={"run_id": args.run_id, "exc_type": type(e).__name__,
                   "exc_msg": str(e), "traceback": tb},
        )
        _emit_failed(
            args.run_id,
            _classify_exception_reason(e),
            f"{type(e).__name__}: {e}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
