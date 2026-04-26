"""Browser-agent subprocess entry — `python -m aesthesis.browser_agent`.

This is the load-bearing one-shot binary the parent (`capture.runner`)
spawns per capture run. It owns:

1. Playwright Chromium launch with ``--remote-debugging-port=PORT`` so
   BrowserUse can attach via ``BrowserSession(cdp_url=...)`` (the only
   coexistence pattern that works for both recording and agent-driving;
   see ASSUMPTIONS.md research log entries A1, A2, A3).

2. Cookie injection (D31 cookies-only auth) before page navigation.

3. CDP screencast via ``capture.streamer.AdaptiveStreamer`` — emits
   each JPEG frame to stdout as JSONL AND stashes raw bytes for the
   MP4 stitch step. Single source of truth for both live UX and the
   archived recording.

4. BrowserUse ``Agent.run()`` wrapped in an ``asyncio.wait`` race
   against a 30-second recording-cap timer (D7). Whichever finishes
   first ends the run. The OUTER 90-second wall-clock SIGKILL is the
   parent's job (D1) and never touches this process — we just trust
   it as the final safety net.

5. ffmpeg-stitch the stashed JPEGs into an H.264 MP4 (validation.py:92
   hard-requires h264) at ``{run_dir}/video.mp4``.

6. Write BrowserUse action history to ``{run_dir}/actions.jsonl`` so
   the orchestrator can stamp ``Event.agent_action_at_t`` per D15.

7. Emit a final ``capture_complete`` JSONL line on stdout, exit 0.
   On any structural failure: emit ``capture_failed`` with a typed
   reason and exit 1. Never swallow; tests fail loudly per project
   memory ``feedback_no_mocks``.

All logging goes to stderr (parent reads stdout for protocol). Verbose
structured logs at every state transition — this subprocess is the
hardest part to debug after the fact.
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
import time
import traceback
from pathlib import Path
from typing import Any

from .capture.protocol import AuthSpec
from .capture.streamer import AdaptiveStreamer
from .logging_config import configure_logging

log = logging.getLogger("aesthesis.browser_agent")


# ─── Argv & logging setup ──────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aesthesis.browser_agent",
        description=(
            "One-shot subprocess: drive a URL with BrowserUse while "
            "recording a CDP screencast to an MP4."
        ),
    )
    p.add_argument("--url", required=True, help="page to navigate")
    p.add_argument("--goal", default=None, help="goal-parameterized agent task; default = generic first-impression")
    p.add_argument("--run-dir", required=True, type=Path, help="working dir for video.mp4 + actions.jsonl")
    p.add_argument("--max-recording-s", type=float, default=30.0, help="hard recording cap (D7)")
    p.add_argument("--headless", type=int, default=1, choices=(0, 1), help="chromium headless mode")
    p.add_argument("--browseruse-model", default="gemini-2.5-pro", help="Gemini model name for BrowserUse Agent")
    p.add_argument("--auth-cookies-file", type=Path, default=None, help="JSON file: list of CookieSpec dicts to set before navigation")
    p.add_argument("--run-id", required=True, help="opaque ID for log correlation; matches parent's run_id")
    p.add_argument("--viewport-width", type=int, default=1280, help="chromium viewport width")
    p.add_argument("--viewport-height", type=int, default=720, help="chromium viewport height")
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


# ─── ffmpeg discovery + stitch ─────────────────────────────────────────────


def _find_ffmpeg() -> str:
    """Return the absolute path to ffmpeg, or raise loudly.

    ASSUMPTION (see ASSUMPTIONS.md A4): ffmpeg must be on PATH or
    available via the ``imageio_ffmpeg`` bundled binary. If neither,
    the build fails. No silent fallback to "skip MP4 encoding."
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
    ffmpeg is missing or returns non-zero (tests must fail, per
    project memory).

    The duration is computed from the actual frame timestamps (last -
    first), not from the nominal target FPS, so a tier-walked stream
    produces an MP4 whose wall-clock duration matches what the user saw.
    Frames are encoded at a constant 10 fps — variable framerate from
    image2pipe is awkward; constant 10 fps slightly compresses time
    when the source dropped below 10 fps, which is acceptable for a
    30-second hackathon clip and documented in ASSUMPTIONS.md A5.
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

    # Use image2pipe demuxer — JPEG bytes piped on stdin become frames.
    # libx264 with veryfast preset + faststart for streamable MP4.
    # yuv420p so consumer ffprobe + browsers accept it everywhere.
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
        "-an",  # no audio track — Aesthesis is video-only post-§17 audio strip
        str(out_path),
    ]
    log.debug("ffmpeg.cmd", extra={"step": "encode", "run_id": run_id, "cmd": " ".join(cmd)})

    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603 — ffmpeg path is shutil.which-validated
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Feed each JPEG. Catch BrokenPipeError (ffmpeg died early) and
    # surface the real ffmpeg stderr so the user sees what went wrong.
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


# ─── Cookie loading ────────────────────────────────────────────────────────


def _load_auth(path: Path | None, *, run_id: str) -> AuthSpec | None:
    if path is None:
        return None
    log.info("auth.load", extra={"step": "auth", "run_id": run_id, "path": str(path)})
    raw = json.loads(path.read_text(encoding="utf-8"))
    auth = AuthSpec(**raw)
    n_cookies = len(auth.cookies or [])
    log.info("auth.loaded", extra={"step": "auth", "run_id": run_id, "n_cookies": n_cookies})
    return auth


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

    browser-use 0.12.x ships its own LLM abstractions (``ChatGoogle``,
    ``ChatOpenAI``, ``ChatAnthropic``, ``ChatBrowserUse``, ...) — we do
    NOT use langchain. ``ChatGoogle`` wraps Google's official ``genai``
    client directly and accepts ``api_key`` as a plain string.

    We pull the key from ``GEMINI_API_KEY`` first (Aesthesis project
    convention, used by the synthesizer too), then fall back to
    ``GOOGLE_API_KEY`` (genai's default lookup name).
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
        temperature=0.0,  # determinism beats creativity for an action-picker
    )


# ─── Action history extraction ─────────────────────────────────────────────


def _serialise_action_history(agent: Any, *, run_id: str) -> list[dict]:
    """Pull a JSON-friendly list of (timestamp, description) actions from
    ``agent.history``.

    Per browser_use/agent/service.py:435, ``Agent.__init__`` always sets
    ``self.history = AgentHistoryList(history=[], usage=None)``. The
    inner ``.history`` attribute is the list of per-step ``AgentHistory``
    items (defined in browser_use/agent/views.py). We prefer
    ``agent_steps()`` when it exists (curated per-step view), then fall
    back to walking ``history.history`` directly. Each step is dumped via
    Pydantic ``model_dump()`` when available, then ``str()`` as a final
    fallback.

    Per-step wall-clock timestamps aren't always exposed by browser-use,
    so we use the step index as a deterministic ordering key. The
    orchestrator's ±0.5s window in ``_nearest_action`` matches each step
    against brain-event TR timestamps; with browser-use steps coming in
    roughly real-time, index-as-second is a usable approximation for the
    short captures we run (~30s).
    """
    history_list = getattr(agent, "history", None)
    if history_list is None:
        log.warning(
            "agent.history_not_found",
            extra={"step": "agent", "run_id": run_id,
                   "agent_attrs": [a for a in dir(agent) if not a.startswith("_")][:25]},
        )
        return []

    # Prefer agent_steps() — the curated per-step view from AgentHistoryList
    steps = None
    if hasattr(history_list, "agent_steps") and callable(history_list.agent_steps):
        try:
            steps = history_list.agent_steps()
        except Exception as e:  # noqa: BLE001 — defensive against API drift
            log.debug("agent.history.agent_steps_failed: %s", e,
                      extra={"step": "agent", "run_id": run_id})
            steps = None

    # Fall back to the raw .history list
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
        # AgentHistory items are Pydantic models with state, model_output,
        # and result fields. Dump to dict, then surface result or
        # model_output as the human-readable description.
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


# ─── Main async runner ─────────────────────────────────────────────────────


async def _run_capture(args: argparse.Namespace) -> None:
    run_id = args.run_id
    run_dir: Path = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)

    auth = _load_auth(args.auth_cookies_file, run_id=run_id)
    cdp_port = _find_free_port()

    log.info(
        "agent.boot",
        extra={
            "step": "agent", "run_id": run_id,
            "url": args.url, "goal_present": args.goal is not None,
            "max_recording_s": args.max_recording_s,
            "headless": bool(args.headless),
            "browseruse_model": args.browseruse_model,
            "cdp_port": cdp_port,
            "viewport": [args.viewport_width, args.viewport_height],
            "n_cookies": len(auth.cookies) if (auth and auth.cookies) else 0,
        },
    )

    # Lazy imports — heavy deps stay out of `python -m aesthesis.browser_agent --help` path
    from playwright.async_api import async_playwright  # type: ignore
    from browser_use import Agent, Browser  # type: ignore — Browser is the modern alias for BrowserSession

    streamer: AdaptiveStreamer | None = None
    pw_ctx = None
    chromium = None

    try:
        async with async_playwright() as pw:
            log.info("agent.playwright_launched", extra={"step": "agent", "run_id": run_id})
            chromium = await pw.chromium.launch(
                headless=bool(args.headless),
                args=[
                    f"--remote-debugging-port={cdp_port}",
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                    "--no-first-run",
                ],
            )
            log.info(
                "agent.chromium_launched",
                extra={"step": "agent", "run_id": run_id, "cdp_port": cdp_port},
            )

            pw_ctx = await chromium.new_context(
                viewport={"width": args.viewport_width, "height": args.viewport_height},
                ignore_https_errors=False,
            )

            if auth and auth.cookies:
                cookie_dicts = [
                    {k: v for k, v in c.model_dump().items() if v is not None}
                    for c in auth.cookies
                ]
                await pw_ctx.add_cookies(cookie_dicts)
                log.info("agent.cookies_injected",
                         extra={"step": "agent", "run_id": run_id, "n_cookies": len(cookie_dicts)})

            page = await pw_ctx.new_page()
            log.info("agent.page_opened", extra={"step": "agent", "run_id": run_id})

            # Open CDP session for the screencast. BrowserUse will get its
            # own session via cdp_url — both can coexist on the same page.
            cdp_session = await pw_ctx.new_cdp_session(page)
            log.info("agent.cdp_session_opened", extra={"step": "agent", "run_id": run_id})

            streamer = AdaptiveStreamer(
                cdp_session, run_id=run_id,
                on_lifecycle=_emit_stdout_async,
            )
            await streamer.start()

            # Initial navigation. BrowserUse will take over after this but
            # we navigate first so the streamer captures the initial paint.
            log.info("agent.initial_nav_begin",
                     extra={"step": "agent", "run_id": run_id, "url": args.url})
            await page.goto(args.url, wait_until="domcontentloaded", timeout=15_000)
            log.info("agent.initial_nav_done", extra={"step": "agent", "run_id": run_id})

            # Build BrowserUse on top. Browser is the modern alias for
            # BrowserSession (per browser_use/__init__.py:54). Connecting via
            # cdp_url means BrowserUse attaches to the Chromium WE launched,
            # rather than spawning its own — the only pattern that lets our
            # CDP screencast and BrowserUse's agent loop coexist on the
            # same tab. (See ASSUMPTIONS_PHASE2_CAPTURE.md A2.)
            llm = _build_llm(args.browseruse_model, run_id=run_id)
            bu_browser = Browser(cdp_url=f"http://127.0.0.1:{cdp_port}")
            log.info("agent.browseruse_session_built",
                     extra={"step": "agent", "run_id": run_id, "cdp_url": f"http://127.0.0.1:{cdp_port}"})

            task_prompt = _build_task_prompt(args.url, args.goal, args.max_recording_s)
            log.debug("agent.task_prompt", extra={"step": "agent", "run_id": run_id, "prompt_len": len(task_prompt)})
            # Agent accepts both `browser=` (preferred modern API) and
            # `browser_session=` (legacy alias). Verified against
            # browser_use/agent/service.py:138-140 in 0.12.6.
            agent = Agent(task=task_prompt, llm=llm, browser=bu_browser)
            log.info("agent.constructed", extra={"step": "agent", "run_id": run_id})

            # Race: agent.run() vs recording cap
            log.info("agent.run_begin",
                     extra={"step": "agent", "run_id": run_id, "max_recording_s": args.max_recording_s})
            agent_task = asyncio.create_task(agent.run(), name="browseruse_agent")
            timer_task = asyncio.create_task(asyncio.sleep(args.max_recording_s), name="recording_cap_timer")
            t_run = time.perf_counter()

            done, pending = await asyncio.wait(
                {agent_task, timer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            run_elapsed = time.perf_counter() - t_run
            winner_name = next(iter(done)).get_name()
            log.info(
                "agent.run_winner",
                extra={"step": "agent", "run_id": run_id,
                       "winner": winner_name, "elapsed_s": round(run_elapsed, 2)},
            )

            for p in pending:
                p.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await p

            # Stop screencast — frames_for_mp4 is now final
            await streamer.stop()

            # Pull action history
            action_log = _serialise_action_history(agent, run_id=run_id)
            actions_path = run_dir / "actions.jsonl"
            actions_path.write_text(
                "\n".join(json.dumps(a, separators=(",", ":")) for a in action_log) + "\n",
                encoding="utf-8",
            )
            log.info(
                "agent.actions_written",
                extra={"step": "agent", "run_id": run_id,
                       "path": str(actions_path), "n_actions": len(action_log)},
            )

            # Encode MP4
            mp4_path = run_dir / "video.mp4"
            duration_s, mp4_size = _encode_frames_to_mp4(
                streamer.frames_for_mp4, mp4_path, run_id=run_id,
            )

            # Finalize: stop CDP cleanly, close context. (Streamer already stopped.)
            await pw_ctx.close()
            await chromium.close()
            log.info("agent.chromium_closed", extra={"step": "agent", "run_id": run_id})

            _emit_stdout({
                "type": "capture_complete",
                "run_id": run_id,
                "duration_s": round(duration_s, 2),
                "mp4_size_bytes": mp4_size,
                "n_actions": len(action_log),
            })
            log.info("agent.capture_complete_emitted", extra={"step": "agent", "run_id": run_id})

    except asyncio.CancelledError:
        # Parent SIGKILL'd us mid-await — best-effort cleanup, then re-raise
        log.warning("agent.cancelled (likely parent SIGKILL grace)",
                    extra={"step": "agent", "run_id": run_id})
        raise


def _classify_exception_reason(exc: BaseException) -> str:
    """Map an exception to a capture_failed.reason value."""
    msg = str(exc)
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in msg.lower():
        # Could be Playwright nav timeout — treat as navigation_error
        # since the parent handles the WALL-clock timeout via SIGKILL.
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
        asyncio.run(_run_capture(args))
        log.info("browser_agent.main_exit_ok", extra={"run_id": args.run_id})
        return 0
    except KeyboardInterrupt:
        _emit_failed(args.run_id, "crashed", "interrupted")
        return 130
    except Exception as e:  # noqa: BLE001 — top-level boundary
        tb = traceback.format_exc()
        log.error(
            "browser_agent.main_exception",
            extra={"run_id": args.run_id, "exc_type": type(e).__name__, "exc_msg": str(e), "traceback": tb},
        )
        _emit_failed(
            args.run_id,
            _classify_exception_reason(e),
            f"{type(e).__name__}: {e}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
