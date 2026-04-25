"""Upload validation — file-size and ffmpeg-fallback paths.

The full ffmpeg path requires ffmpeg-python + an actual MP4 file. We
skip those if the dep isn't present; they're exercised by the integration
test environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aesthesis.config import AppConfig
from aesthesis.validation import validate_upload


def test_missing_file_fails(tmp_path: Path):
    cfg = AppConfig()
    res = validate_upload(tmp_path / "nope.mp4", cfg)
    assert not res.ok
    assert "not found" in (res.error or "")


def test_zero_size_fails(tmp_path: Path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    cfg = AppConfig()
    res = validate_upload(p, cfg)
    assert not res.ok
    assert "empty" in (res.error or "").lower()


def test_oversize_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "huge.mp4"
    p.write_bytes(b"\x00" * 1024)  # 1 KB
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")  # 10 bytes max
    # Re-construct config so it picks up the new env var.
    cfg = AppConfig()
    res = validate_upload(p, cfg)
    assert not res.ok
    assert "max" in (res.error or "").lower()


def test_no_ffmpeg_falls_through(tmp_path: Path):
    """If ffmpeg-python isn't installed, validate_upload should accept
    on header-only — so /api/analyze still works in mock-mode dev."""
    try:
        import ffmpeg  # type: ignore  # noqa: F401
        pytest.skip("ffmpeg-python is installed; this test exercises the "
                    "fallback path")
    except ImportError:
        pass
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"\x00\x01" * 200)
    cfg = AppConfig()
    res = validate_upload(p, cfg)
    assert res.ok
    assert res.codec == "unknown"
