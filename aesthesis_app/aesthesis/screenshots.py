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


def _ffmpeg_seek_extract(
    video: Path, t_s: float, out_path: Path, *, fast_seek: bool,
) -> tuple[bool, str]:
    """Run ffmpeg to extract one frame at ``t_s``.

    ``fast_seek=True``  → ``-ss`` before ``-i`` (sub-second, may land on a
                          keyframe up to ~1 GOP earlier; can fail near EOF).
    ``fast_seek=False`` → ``-ss`` after ``-i`` (decodes from t=0 to t_s, slower
                          but exact and never misses near-EOF frames).

    Returns ``(ok, stderr_excerpt)``. Never raises.
    """
    if fast_seek:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t_s:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=800:-2",
            "-q:v", "5",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-ss", f"{t_s:.3f}",
            "-frames:v", "1",
            "-vf", "scale=800:-2",
            "-q:v", "5",
            str(out_path),
        ]
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
    """CLI fallback used when ``ffmpeg-python`` isn't installed. Tries the
    fast-seek path first, then the slow exact-seek path before giving up.
    """
    ok, err_fast = _ffmpeg_seek_extract(video, t_s, out_path, fast_seek=True)
    if ok:
        return True
    log.debug("ffmpeg fast-seek failed at t=%.2f (%s) — retrying with slow seek",
              t_s, err_fast)
    ok, err_slow = _ffmpeg_seek_extract(video, t_s, out_path, fast_seek=False)
    if ok:
        return True
    log.info(
        "ffmpeg cli could not extract frame at t=%.2fs — fast=%r slow=%r",
        t_s, err_fast, err_slow,
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

    # Path A: ffmpeg-python wrapper.
    py_err: str | None = None
    try:
        import ffmpeg  # type: ignore
        try:
            (
                ffmpeg
                .input(str(video), ss=t_s)
                .output(str(out_path), vframes=1, vf="scale=800:-2", **{"q:v": 5})
                .overwrite_output()
                .run(quiet=True)
            )
            if out_path.exists() and out_path.stat().st_size > 0:
                log.debug("extracted frame via ffmpeg-python",
                          extra={"step": "screenshot", "t_s": t_s,
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
