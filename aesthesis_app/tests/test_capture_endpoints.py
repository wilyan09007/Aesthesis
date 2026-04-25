"""Phase 2 endpoint tests.

NO MOCKS. Two layers:

- ``test_*_fast`` — exercise the FastAPI app surface that doesn't need a
  live subprocess: cached-demos manifest parsing, video 404 paths,
  by-run 404, /api/cached-demos path-traversal guard. These run without
  Gemini, Chromium, or ffmpeg.

- ``test_*_integration`` — full end-to-end: POST /api/run spawns a real
  subprocess, WS connect receives real binary frames + lifecycle events,
  the 409 cap is exercised, etc. These require all fixtures and may
  take ~30-60 seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aesthesis.capture import runner as capture_runner
from aesthesis.config import AppConfig, get_config
import aesthesis.config as config_module


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_capture_registry():
    """Module-level _REGISTRY is shared across tests — wipe between each
    so a hung run from a previous test doesn't pollute the next."""
    yield
    for run_id in list(capture_runner._REGISTRY.keys()):
        runner = capture_runner._REGISTRY.pop(run_id, None)
        if runner and runner.proc:
            try:
                runner.proc.kill()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch):
    """Force a per-test upload_dir + cached_demos_dir so file-on-disk
    state from one test never leaks into the next.
    """
    cached = tmp_path / "cached"
    cached.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CACHED_DEMOS_DIR", str(cached))
    monkeypatch.setenv("CHROMIUM_HEADLESS", "1")
    monkeypatch.setenv("CAPTURE_MAX_WALL_S", "10")
    monkeypatch.setenv("CAPTURE_RECORDING_CAP_S", "8")
    # Reset the singleton config so the env vars take effect
    config_module._config = None
    cfg = get_config()
    yield cfg
    config_module._config = None


@pytest.fixture
def client(isolated_config: AppConfig) -> TestClient:  # noqa: ARG001
    # Import inside the fixture so isolated_config's env vars are picked up
    from aesthesis.main import app
    return TestClient(app)


# ─── Fast tests (no subprocess) ────────────────────────────────────────────


def test_health_endpoint_works(client: TestClient) -> None:
    """Sanity: /health still returns 200 with the new endpoints in place."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_get_run_video_returns_404_for_unknown_run(client: TestClient) -> None:
    r = client.get("/api/run/nonexistent-run-id/video")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["error"] == "video_not_ready"


def test_analyze_by_run_returns_404_when_no_video(client: TestClient) -> None:
    r = client.post("/api/analyze/by-run/nonexistent-run-id", json={})
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["error"] == "video_not_ready"


def test_cached_demos_empty_when_no_manifest(client: TestClient) -> None:
    """No MANIFEST.json -> empty list. Front-end falls through to the
    'no fallback available' state."""
    r = client.get("/api/cached-demos")
    assert r.status_code == 200
    assert r.json() == []


def test_cached_demos_lists_manifest_entries(client: TestClient, isolated_config: AppConfig) -> None:
    manifest = isolated_config.cached_demos_dir / "MANIFEST.json"
    manifest.write_text(json.dumps([
        {"url": "https://example.com", "label": "Example", "mp4_filename": "demo1.mp4"},
        {"url": "https://other.com", "label": "Other", "mp4_filename": "demo2.mp4"},
    ]), encoding="utf-8")

    r = client.get("/api/cached-demos")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 2
    assert entries[0]["url"] == "https://example.com"
    assert entries[1]["mp4_filename"] == "demo2.mp4"


def test_cached_demo_download_serves_file(client: TestClient, isolated_config: AppConfig) -> None:
    mp4_path = isolated_config.cached_demos_dir / "test.mp4"
    fake_mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x01"  # MP4-ish header
    mp4_path.write_bytes(fake_mp4_bytes)

    r = client.get("/api/cached-demos/test.mp4")
    assert r.status_code == 200
    assert r.content == fake_mp4_bytes
    assert r.headers["content-type"] == "video/mp4"


def test_cached_demo_download_rejects_path_traversal(client: TestClient) -> None:
    """Don't let a malicious filename walk out of cached_demos_dir."""
    for bad in ("../etc/passwd", "..\\windows\\system32", "subdir/file.mp4", "/abs/path"):
        r = client.get(f"/api/cached-demos/{bad}")
        # Either 400 (rejected by guard) or 404 (filename has no traversal
        # but file isn't there) — both are safe outcomes.
        assert r.status_code in (400, 404), (
            f"path-traversal '{bad}' got status {r.status_code} — "
            f"the guard in main.download_cached_demo missed it."
        )


def test_cached_demo_download_404_for_missing_file(client: TestClient) -> None:
    r = client.get("/api/cached-demos/does-not-exist.mp4")
    assert r.status_code == 404


def test_post_run_with_invalid_url_returns_422(client: TestClient) -> None:
    """Pydantic's HttpUrl rejects non-URL inputs at the schema layer."""
    r = client.post("/api/run", json={"url": "not a url at all"})
    assert r.status_code == 422


# ─── Integration tests (real subprocess + WS) ──────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_run_returns_409(
    isolated_config: AppConfig,
    gemini_api_key: str,         # noqa: ARG001
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
) -> None:
    """D19: cap=1. Second concurrent POST /api/run gets 409."""
    from aesthesis.main import app
    from aesthesis.capture.protocol import RunRequest

    # Direct call (no TestClient — we need to await async runner.start)
    req1 = RunRequest(url="http://192.0.2.1", goal="hold the slot")
    runner1 = await capture_runner.start_run(req1, cfg=isolated_config)
    try:
        # Now the second call should hit the 409 guard
        req2 = RunRequest(url="http://192.0.2.2", goal="should be rejected")
        with pytest.raises(capture_runner.CaptureInProgressError) as exc_info:
            await capture_runner.start_run(req2, cfg=isolated_config)
        assert exc_info.value.active_run_id == runner1.run_id
    finally:
        # Clean up
        if runner1.proc:
            runner1.proc.kill()
            try:
                await asyncio.wait_for(runner1.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        runner1._kill_chromium_zombies()
        capture_runner._REGISTRY.pop(runner1.run_id, None)
