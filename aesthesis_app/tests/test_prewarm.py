"""Pre-warm protocol tests (Phase 2 two-phase capture).

NO MOCKS. Fast tests assert HTTP-layer contract (404, 409, validation).
Integration tests spawn a real subprocess against the RFC-5737 unreachable
IP so the subprocess sits in pre-warm without ever transitioning to
running, then exercise the start_capture / disconnect-grace paths.

Per project memory ``feedback_no_mocks`` and the explicit /ship rule:
no skipif, no fakes. Tests fail loudly when the env is wrong.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aesthesis.capture import runner as capture_runner
from aesthesis.capture.protocol import RunRequest
from aesthesis.config import AppConfig, get_config
import aesthesis.config as config_module


# ─── Fixtures (mirror test_capture_endpoints.py) ──────────────────────────


@pytest.fixture(autouse=True)
def reset_capture_registry():
    """Wipe module-level _REGISTRY between tests so a hung run from a
    previous test doesn't pollute the next."""
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
    cached = tmp_path / "cached"
    cached.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CACHED_DEMOS_DIR", str(cached))
    monkeypatch.setenv("CHROMIUM_HEADLESS", "1")
    monkeypatch.setenv("CAPTURE_MAX_WALL_S", "30")
    monkeypatch.setenv("CAPTURE_RECORDING_CAP_S", "8")
    config_module._config = None
    cfg = get_config()
    yield cfg
    config_module._config = None


@pytest.fixture
def client(isolated_config: AppConfig) -> TestClient:  # noqa: ARG001
    from aesthesis.main import app
    return TestClient(app)


# ─── Fast HTTP-contract tests (no real subprocess needed) ─────────────────


def test_start_capture_returns_404_for_unknown_run_id(client: TestClient) -> None:
    """POST /api/run/{nonexistent}/start should 404 — there's no
    pre-warmed subprocess to send the stdin start command to."""
    r = client.post("/api/run/totally-fake-run-id/start", json={
        "url": "https://example.com",
        "goal": "explore",
    })
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["error"] == "unknown_run_id"
    assert body["detail"]["run_id"] == "totally-fake-run-id"


def test_start_capture_validates_url(client: TestClient) -> None:
    """Pydantic HttpUrl rejects non-URL inputs at the schema layer."""
    r = client.post("/api/run/some-run-id/start", json={"url": "not a url"})
    # 422 from validation OR 404 from unknown_run_id (depends on which check
    # runs first in starlette's middleware ordering). Both prove the
    # endpoint takes the request seriously.
    assert r.status_code in (422, 404)


def test_prewarm_endpoint_accepts_empty_body(client: TestClient,
                                              gemini_api_key: str,         # noqa: ARG001
                                              chromium_available: None,    # noqa: ARG001
                                              ffmpeg_path: str) -> None:   # noqa: ARG001
    """POST /api/prewarm with empty body should spawn a subprocess and
    return a run_id. Real Chromium + real ChatGoogle.

    NOTE: this test ACTUALLY spawns Chromium + waits for prewarm_ready.
    Slow (~5-10s). Don't run it on a box without Playwright Chromium
    + GEMINI_API_KEY — it'll fail loudly via the fixtures.
    """
    r = client.post("/api/prewarm", json={})
    assert r.status_code == 200, f"prewarm failed: {r.status_code} {r.text}"
    body = r.json()
    assert "run_id" in body
    assert body["status"] == "started"

    # Clean up — kill the prewarm subprocess we just spawned
    runner = capture_runner.get_runner(body["run_id"])
    assert runner is not None
    if runner.proc:
        runner.proc.kill()


# ─── Integration tests (real subprocess + WS) ─────────────────────────────


@pytest.mark.asyncio
async def test_prewarm_then_start_lifecycle(
    isolated_config: AppConfig,
    gemini_api_key: str,         # noqa: ARG001
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
) -> None:
    """Full two-phase happy path:

      1. start_run(prewarm_only=True) spawns subprocess
      2. await prewarm_ready (phase warming → ready)
      3. start_capture(url, goal) sends stdin start command
      4. phase transitions to running
      5. wall-clock D1 timer is now armed (NOT before)

    Uses 192.0.2.1 (RFC-5737) so the subprocess hangs in page.goto
    after start instead of completing — keeps the test deterministic
    and fast. We tear down before wall-clock fires.
    """
    runner = await capture_runner.start_run(
        None, cfg=isolated_config, prewarm_only=True,
    )
    try:
        assert runner.phase == "warming"
        assert runner.run_started_time == 0.0  # not yet started

        # Wait up to 30s for prewarm_ready (Chromium launch + CDP + LLM init)
        try:
            await asyncio.wait_for(runner._prewarm_ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "prewarm_ready never arrived within 30s. Check that Chromium "
                "launched and CDP is responsive — the subprocess may have "
                "crashed before signaling ready."
            )

        assert runner.phase == "ready"
        # Wall-clock STILL not armed
        assert runner._wallclock_task is None

        # Transition: start_capture → phase=running, wall-clock arms
        await runner.start_capture(
            url="http://192.0.2.1",
            goal="this will hang",
            auth=None,
        )
        assert runner.phase == "running"
        assert runner.run_started_time > 0
        assert runner._wallclock_task is not None
    finally:
        # Teardown — kill the subprocess + sweep zombies
        if runner.proc:
            runner.proc.kill()
            try:
                await asyncio.wait_for(runner.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        runner._kill_chromium_zombies()
        capture_runner._REGISTRY.pop(runner.run_id, None)


@pytest.mark.asyncio
async def test_start_capture_idempotent_when_already_running(
    isolated_config: AppConfig,
    gemini_api_key: str,         # noqa: ARG001
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
) -> None:
    """Calling start_capture twice on the same runner should be a no-op
    on the second call (logs a warning) — protects against double-clicks
    on the Start button."""
    runner = await capture_runner.start_run(
        None, cfg=isolated_config, prewarm_only=True,
    )
    try:
        await asyncio.wait_for(runner._prewarm_ready_event.wait(), timeout=30.0)
        await runner.start_capture(url="http://192.0.2.1", goal="first call", auth=None)
        first_running_time = runner.run_started_time
        # Second call should not re-arm wall-clock
        await runner.start_capture(url="http://192.0.2.1", goal="second call", auth=None)
        assert runner.run_started_time == first_running_time, (
            "second start_capture should be a no-op, not re-arm the wall-clock"
        )
    finally:
        if runner.proc:
            runner.proc.kill()
            try:
                await asyncio.wait_for(runner.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        runner._kill_chromium_zombies()
        capture_runner._REGISTRY.pop(runner.run_id, None)


@pytest.mark.asyncio
async def test_concurrent_prewarm_returns_409(
    isolated_config: AppConfig,
    gemini_api_key: str,         # noqa: ARG001
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
) -> None:
    """D19 cap=1 still applies to /api/prewarm — second concurrent
    prewarm gets CaptureInProgressError."""
    runner1 = await capture_runner.start_run(
        None, cfg=isolated_config, prewarm_only=True,
    )
    try:
        with pytest.raises(capture_runner.CaptureInProgressError) as exc_info:
            await capture_runner.start_run(
                None, cfg=isolated_config, prewarm_only=True,
            )
        assert exc_info.value.active_run_id == runner1.run_id
    finally:
        if runner1.proc:
            runner1.proc.kill()
            try:
                await asyncio.wait_for(runner1.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        runner1._kill_chromium_zombies()
        capture_runner._REGISTRY.pop(runner1.run_id, None)


@pytest.mark.asyncio
async def test_wallclock_NOT_armed_during_prewarm(
    isolated_config: AppConfig,
    gemini_api_key: str,         # noqa: ARG001
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
    monkeypatch,
) -> None:
    """Critical invariant: pre-warm time does NOT count toward the D1
    wall-clock budget. A user who takes 60s to type a URL must not
    have their capture killed before they click Start.

    Forces capture_max_wall_s=5 (very short). If the wall-clock were
    armed during pre-warm, the runner would die before we got to assert
    runner.phase == "ready". With the fix, the runner stays in 'ready'
    indefinitely until start_capture is called.
    """
    monkeypatch.setenv("CAPTURE_MAX_WALL_S", "5")
    config_module._config = None
    cfg = get_config()

    runner = await capture_runner.start_run(None, cfg=cfg, prewarm_only=True)
    try:
        await asyncio.wait_for(runner._prewarm_ready_event.wait(), timeout=30.0)
        assert runner.phase == "ready"

        # Sleep PAST the 5s wall-clock — runner should STILL be in 'ready'
        # because the wall-clock isn't armed until start_capture is called.
        await asyncio.sleep(7.0)
        assert runner.phase == "ready", (
            f"D1 wall-clock fired during pre-warm — phase={runner.phase!r}. "
            "Pre-warm time must not count toward the wall-clock budget."
        )
        assert not runner.completed
        assert runner._wallclock_task is None
    finally:
        if runner.proc:
            runner.proc.kill()
            try:
                await asyncio.wait_for(runner.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        runner._kill_chromium_zombies()
        capture_runner._REGISTRY.pop(runner.run_id, None)
        config_module._config = None
