"""Extract a screenshot at a specific timestamp from an MP4 via ffmpeg.

Used by the synthesizer to attach a per-event screenshot to each Gemini
input event (DESIGN.md §4.5 step 2: "feed the LLM three things per event:
the event record, the screenshot, the agent action log").

If `ffmpeg-python` is unavailable, returns `None` for every event — the
synthesizer sends the event payload to Gemini without an attached image.
"""

from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _run_ffmpeg_cli(video: Path, t_s: float, out_path: Path) -> bool:
    """Fallback path that shells out to the ffmpeg binary directly. Used
    when the `ffmpeg-python` Python wrapper isn't installed but the system
    still has the binary (common case)."""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t_s:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=800:-2",
            "-q:v", "5",
            str(out_path),
        ]
        result = subprocess.run(  # noqa: S603 — args are not user-controlled
            cmd, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            log.debug("ffmpeg cli stderr: %s", result.stderr.decode(errors="replace"))
            return False
        return out_path.exists()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("ffmpeg cli not usable: %s", e)
        return False


def extract_frame(video: Path, t_s: float, out_path: Path) -> Path | None:
    """Save a JPEG of `video` at `t_s` (seconds) to `out_path`. Returns
    the path on success, None if extraction failed.

    Tries `ffmpeg-python` first, falls back to the ffmpeg CLI, then gives
    up. Never raises — failure is silent so a partial result still ships.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import ffmpeg  # type: ignore
        (
            ffmpeg
            .input(str(video), ss=t_s)
            .output(str(out_path), vframes=1, vf="scale=800:-2", **{"q:v": 5})
            .overwrite_output()
            .run(quiet=True)
        )
        if out_path.exists() and out_path.stat().st_size > 0:
            log.debug("extracted frame", extra={"step": "screenshot",
                                                "t_s": t_s, "path": str(out_path)})
            return out_path
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        log.debug("ffmpeg-python failed (%s); trying CLI", e)

    if _run_ffmpeg_cli(video, t_s, out_path):
        log.debug("extracted frame via cli", extra={"step": "screenshot", "t_s": t_s})
        return out_path

    log.info(
        "frame extraction unavailable for t=%.2fs (no ffmpeg) — continuing without screenshot",
        t_s,
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
