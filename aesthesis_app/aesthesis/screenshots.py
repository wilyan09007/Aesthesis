"""Extract a screenshot at a specific timestamp from an MP4 via ffmpeg.

Used by the synthesizer to attach a per-event screenshot to each Gemini
input event (DESIGN.md §4.5 step 2: "feed the LLM three things per event:
the event record, the screenshot, the agent action log"). Without a
screenshot Gemini is reasoning about ROI activations alone and loses the
ability to ground "the user paused on this control" in actual UI pixels.

Diagnostics policy: on failure we log the *actual* ffmpeg stderr at INFO
so production tells us why a timestamp didn't extract. The previous
``"no ffmpeg"`` message conflated three different failure modes (binary
missing, fast-seek lands past EOF, codec mismatch) and made it look like
the image was misconfigured even when ffmpeg was healthy.

Resolution: scaled to ``min(1600, native_width)``. Most screen recordings
arrive at 1920x1080 or higher; capping at 1600 preserves enough pixel
density that small UI elements (16-24px icons, dense table rows, tiny
labels) stay legible to Gemini's vision pass while keeping payload size
sane. The previous 800px cap dropped element-id recall on dense UIs —
see ASSUMPTIONS_AGENT_PROMPT.md §21.2 for the resolution / token-cost
analysis behind the bump.
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


#: Subprocess timeout for the ffmpeg CLI calls. With ``-ss`` before
#: ``-i`` ffmpeg does a fast keyframe seek and one frame extraction —
#: well under a second on a 30s clip. The 10s ceiling that used to live
#: here was cargo-culted defensive padding; on Modal it became the
#: dominant tail when extractions failed in series. Keep it tight so
#: a single bad timestamp can't blow the orchestrator's request budget.
FFMPEG_CLI_TIMEOUT_S: float = 2.0


def _truncate_stderr(b: bytes, n: int = 300) -> str:
    s = b.decode(errors="replace").strip()
    return s if len(s) <= n else s[:n] + f" […+{len(s)-n} more]"


#: Pre-seek window for the combined fast-and-accurate seek mode. We
#: keyframe-seek to (t - this) then decode forward by exactly this many
#: seconds. 1.0s is enough to clear the typical screen-recording GOP
#: (~30 frames at 30fps) without paying for a full from-start decode.
_COMBINED_SEEK_PRE_S: float = 1.0


def _ffmpeg_seek_extract(
    video: Path, t_s: float, out_path: Path, *, mode: str,
) -> tuple[bool, str]:
    """Run ffmpeg to extract one frame at ``t_s``.

    Three seek modes:

    ``mode="combined"`` (default, recommended) — ``-ss`` BEFORE ``-i`` to
        keyframe-seek to ~1s before t_s, then ``-ss`` AFTER ``-i`` to
        decode forward by exactly that delta. Fast (~0.1-0.3s) AND
        frame-exact. The pattern most ffmpeg tutorials recommend for
        thumbnailing.

    ``mode="fast"`` — ``-ss`` before ``-i`` only. Sub-second seek, but
        lands on a keyframe up to ~1 GOP earlier than t_s (so a 30fps
        screen recording with GOP=30 might give a frame ~1s before the
        requested time). This is what we used to use; it's why
        screenshots looked "off" — the frame at "t=10.5s" was actually
        from the keyframe at ~t=9.6s.

    ``mode="slow"`` — ``-ss`` after ``-i`` only. Decodes from frame 0 to
        t_s. Slowest but always exact, including near EOF. Last-resort
        fallback when combined fails.

    Returns ``(ok, stderr_excerpt)``. Never raises.
    """
    pre = max(0.0, t_s - _COMBINED_SEEK_PRE_S)
    delta = t_s - pre
    common_tail = [
        "-frames:v", "1",
        "-vf", "scale='min(1600,iw)':-2",
        "-q:v", "5",
        str(out_path),
    ]
    if mode == "combined":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{pre:.3f}",
            "-i", str(video),
            "-ss", f"{delta:.3f}",
            *common_tail,
        ]
    elif mode == "fast":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t_s:.3f}",
            "-i", str(video),
            *common_tail,
        ]
    elif mode == "slow":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-ss", f"{t_s:.3f}",
            *common_tail,
        ]
    else:
        return False, f"unknown seek mode: {mode!r}"

    try:
        result = subprocess.run(  # noqa: S603 — args are not user-controlled
            cmd, capture_output=True, timeout=FFMPEG_CLI_TIMEOUT_S,
        )
    except FileNotFoundError:
        return False, "ffmpeg binary not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {FFMPEG_CLI_TIMEOUT_S}s"

    if result.returncode != 0:
        return False, _truncate_stderr(result.stderr)
    if not out_path.exists() or out_path.stat().st_size == 0:
        return False, "ffmpeg returned 0 but produced no output file"
    return True, ""


def _run_ffmpeg_cli(video: Path, t_s: float, out_path: Path) -> bool:
    """CLI fallback used when ``ffmpeg-python`` isn't installed.

    Order: combined (fast + frame-exact) → slow (exact, never misses EOF).
    Pure fast-seek is no longer used; it lands on the prior keyframe and
    silently produces frames up to ~1s before the requested time.
    """
    ok, err_combined = _ffmpeg_seek_extract(
        video, t_s, out_path, mode="combined",
    )
    if ok:
        return True
    log.debug(
        "ffmpeg combined-seek failed at t=%.2f (%s) — retrying with slow seek",
        t_s, err_combined,
    )
    ok, err_slow = _ffmpeg_seek_extract(
        video, t_s, out_path, mode="slow",
    )
    if ok:
        return True
    log.info(
        "ffmpeg cli could not extract frame at t=%.2fs — combined=%r slow=%r",
        t_s, err_combined, err_slow,
    )
    return False


def extract_frame(video: Path, t_s: float, out_path: Path) -> Path | None:
    """Save a JPEG of ``video`` at ``t_s`` (seconds) to ``out_path``.

    Returns the path on success, None if every extraction strategy failed.
    Tries the ``ffmpeg-python`` wrapper first, then the CLI with fast seek,
    then the CLI with slow seek (for timestamps near the actual EOF that
    fast seek can't land on). Never raises.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Path A: ffmpeg-python wrapper, using the combined fast+exact seek
    # pattern. Pre-seek to ~1s before t_s (-ss on input is fast keyframe
    # seek), then decode forward to the exact delta (-ss on output).
    # Pure ``ss=t_s`` on the input alone landed on the prior keyframe,
    # which on screen recordings with GOP=30 silently produced frames
    # up to ~1s before the requested time.
    py_err: str | None = None
    pre = max(0.0, t_s - _COMBINED_SEEK_PRE_S)
    delta = t_s - pre
    try:
        import ffmpeg  # type: ignore
        try:
            (
                ffmpeg
                .input(str(video), ss=pre)
                .output(
                    str(out_path),
                    ss=delta,
                    vframes=1,
                    vf="scale='min(1600,iw)':-2",
                    **{"q:v": 5},
                )
                .overwrite_output()
                .run(quiet=True)
            )
            if out_path.exists() and out_path.stat().st_size > 0:
                log.debug("extracted frame via ffmpeg-python (combined seek)",
                          extra={"step": "screenshot", "t_s": t_s,
                                 "pre": pre, "delta": delta,
                                 "bytes": out_path.stat().st_size})
                return out_path
            py_err = "ffmpeg-python returned 0 but produced no output"
        except Exception as e:  # noqa: BLE001
            py_err = f"{type(e).__name__}: {e}"
    except ImportError:
        py_err = "ffmpeg-python not installed"

    # Path B: CLI (covers near-EOF timestamps via the slow-seek retry).
    if _run_ffmpeg_cli(video, t_s, out_path):
        log.debug("extracted frame via ffmpeg cli",
                  extra={"step": "screenshot", "t_s": t_s,
                         "bytes": out_path.stat().st_size})
        return out_path

    # Last resort: was ffmpeg even on the PATH? Distinguishes "broken
    # extraction" from "broken image" so the operator doesn't go on a
    # ffmpeg-missing wild goose chase when the binary is fine.
    binary_status = (
        "missing" if shutil.which("ffmpeg") is None else "present"
    )
    log.info(
        "frame extraction failed at t=%.2fs (ffmpeg binary=%s, ffmpeg-python err=%r) "
        "— continuing without screenshot",
        t_s, binary_status, py_err,
    )
    return None


def encode_frame_b64(path: Path) -> str | None:
    """Read a JPEG and return its base64 string, or None if missing."""
    if not path or not path.exists():
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as e:  # noqa: BLE001
        log.warning("could not read screenshot %s: %s", path, e)
        return None
