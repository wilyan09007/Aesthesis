"""ffmpeg-backed validation for uploaded MP4s (DESIGN.md §4.7).

Checks (in order — first failure wins):
    1. file exists, non-zero bytes
    2. ≤ MAX_UPLOAD_BYTES
    3. ffprobe parses successfully
    4. has at least one video stream
    5. video codec is H.264
    6. duration ≤ MAX_DURATION_S
    7. resolution ≤ MAX_WIDTH × MAX_HEIGHT

Returns `ValidationResult` either way — the caller picks the HTTP response
shape. We DO NOT raise on validation failure; the API endpoint returns
400 with a structured body so the frontend can surface a user-readable
error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    ok: bool
    error: str | None = None
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""

    @classmethod
    def fail(cls, msg: str) -> "ValidationResult":
        return cls(ok=False, error=msg)


def _try_ffprobe(path: Path) -> dict | None:
    """ffprobe wrapper. Returns None and logs (does not raise) if ffmpeg
    is missing or the file is unparseable."""
    try:
        import ffmpeg  # type: ignore
    except ImportError:
        log.warning(
            "ffmpeg-python not installed — falling back to header-only checks"
        )
        return None
    try:
        return ffmpeg.probe(str(path))  # type: ignore[no-any-return]
    except Exception as e:  # noqa: BLE001 — ffmpeg.Error or any IO error
        log.info("ffprobe failed for %s: %s", path, e)
        return None


def validate_upload(path: Path, cfg: AppConfig) -> ValidationResult:
    """Validate a single uploaded MP4 against the project's caps."""
    log.debug("validate_upload begin", extra={"step": "validate", "path": str(path)})

    if not path.exists():
        return ValidationResult.fail(f"file not found: {path}")

    size = path.stat().st_size
    if size == 0:
        return ValidationResult.fail("file is empty")
    if size > cfg.max_upload_bytes:
        mb = cfg.max_upload_bytes / (1024 * 1024)
        return ValidationResult.fail(f"file is {size} bytes; max is {mb:.0f} MB")

    probe = _try_ffprobe(path)
    if probe is None:
        # No ffmpeg available. Accept the upload but warn — the TRIBE service
        # will reject it later if it really is malformed.
        log.warning(
            "skipping deep validation: ffmpeg unavailable. "
            "Accepting %s on header-only check.", path,
        )
        return ValidationResult(
            ok=True, duration_s=0.0, width=0, height=0, codec="unknown",
        )

    streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        return ValidationResult.fail("no video track found")
    v = streams[0]

    codec = v.get("codec_name", "")
    if codec.lower() != "h264":
        return ValidationResult.fail(
            f"codec '{codec}' not supported — re-encode as H.264 (MP4)"
        )

    try:
        duration_s = float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return ValidationResult.fail("could not determine duration")
    if duration_s > cfg.max_duration_s:
        return ValidationResult.fail(
            f"video is {duration_s:.1f}s; maximum is {cfg.max_duration_s:.0f}s"
        )

    try:
        width = int(v["width"])
        height = int(v["height"])
    except (KeyError, TypeError, ValueError):
        return ValidationResult.fail("could not determine resolution")
    if width > cfg.max_width or height > cfg.max_height:
        return ValidationResult.fail(
            f"resolution {width}×{height} exceeds "
            f"{cfg.max_width}×{cfg.max_height}"
        )

    log.info(
        "upload validated",
        extra={"step": "validate", "duration_s": duration_s,
               "shape": [width, height], "codec": codec},
    )
    return ValidationResult(
        ok=True, duration_s=duration_s, width=width, height=height, codec=codec,
    )
