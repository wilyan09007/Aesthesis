"""R4 — SIGKILL regression test (D1 + D26).

Mandatory per /plan-eng-review's regression rule. The whole capture path
is gated on the kill chain working: BrowserUse is documented to ignore
internal timeouts (browser-use issues #1157, #3615, #2808, #839), so the
parent's external SIGKILL is the only safety net keeping a hung agent
from blocking subsequent runs forever.

NO MOCKS. The test launches the real ``aesthesis.browser_agent``
subprocess via the real ``CaptureRunner`` against RFC-5737 IP
``192.0.2.1`` (reserved for documentation/test use, guaranteed to never
respond). The subprocess starts real Chromium, hangs in
``page.goto(...)`` (or in ``agent.run()`` if the LLM keeps the loop
alive), and the parent's wall-clock SIGKILL is the only thing that
cuts it down.

Verifies:

1. ``capture_max_wall_s`` triggers SIGKILL within budget
2. Subprocess returncode is non-zero (i.e., it was killed, didn't exit
   cleanly)
3. No Chromium zombie processes remain (D26 by-pid + by-name sweep)
4. A second ``CaptureRunner.start()`` launches without resource conflict
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import psutil
import pytest

from aesthesis.capture import runner as capture_runner
from aesthesis.capture.protocol import RunRequest
from aesthesis.config import AppConfig


def _chromium_processes_now() -> set[int]:
    """Snapshot all chromium-named processes currently running. Used to
    diff before/after to verify the test didn't leak."""
    out: set[int] = set()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            n = (p.info.get("name") or "").lower()
            if n in ("chrome.exe", "chromium.exe", "chrome", "chromium"):
                out.add(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


@pytest.mark.asyncio
async def test_R4_sigkill_kills_hung_capture(
    tmp_path: Path,
    gemini_api_key: str,         # noqa: ARG001 — required env (BrowserUse needs LLM)
    chromium_available: None,    # noqa: ARG001
    ffmpeg_path: str,            # noqa: ARG001
) -> None:
    """The load-bearing test for D1. Must pass before /ship can land."""
    # Snapshot chromium processes before — anything new we see after the
    # test must be killed by us, not pre-existing.
    chromium_before = _chromium_processes_now()

    cfg = AppConfig(
        upload_dir=tmp_path / "uploads",  # type: ignore[arg-type]
        capture_max_wall_s=10.0,           # short wall-clock so the test runs fast
        capture_recording_cap_s=8.0,       # inner cap also short
        chromium_headless=True,
    )
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)

    request = RunRequest(
        url="http://192.0.2.1",  # RFC-5737 TEST-NET-1: guaranteed unreachable
        goal="this should never complete",
    )

    t0 = time.monotonic()
    runner = await capture_runner.start_run(request, cfg=cfg)

    # Wait up to 30s for the subprocess to die (wall-clock fires at 10s,
    # plus subprocess + ffmpeg cleanup buffer).
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if runner.proc and runner.proc.returncode is not None:
            break
        await asyncio.sleep(0.5)
    elapsed = time.monotonic() - t0

    # Assertions
    assert runner.proc is not None, "subprocess was never spawned"
    assert runner.proc.returncode is not None, (
        f"subprocess did not exit within 30s — kill chain is broken. "
        f"pid={runner.proc.pid}, elapsed={elapsed:.1f}s"
    )
    assert runner.proc.returncode != 0, (
        f"subprocess exited cleanly with rc=0 — SIGKILL did not fire. "
        f"That means BrowserUse returned naturally OR the kill bypass works. "
        f"elapsed={elapsed:.1f}s"
    )
    assert elapsed >= cfg.capture_max_wall_s - 1.0, (
        f"subprocess died too quickly ({elapsed:.1f}s) — wall-clock timer "
        f"({cfg.capture_max_wall_s}s) didn't actually run. Possible the "
        f"subprocess crashed before SIGKILL had a chance, which is fine for "
        f"shutdown semantics but means we didn't actually exercise the "
        f"timeout path."
    )

    # D26 — no Chromium zombies should survive. Allow up to 3s for the
    # OS to reap killed processes.
    deadline_clean = time.monotonic() + 3.0
    leaked: set[int] = set()
    while time.monotonic() < deadline_clean:
        chromium_after = _chromium_processes_now()
        leaked = chromium_after - chromium_before
        if not leaked:
            break
        await asyncio.sleep(0.5)
    assert not leaked, (
        f"D26 violation: {len(leaked)} Chromium process(es) survived the "
        f"kill chain — pids: {sorted(leaked)}. The by-pid + by-name sweep "
        f"in CaptureRunner._kill_chromium_zombies isn't reaching them."
    )

    # Verify a SECOND run can launch (registry was cleared, no port collisions)
    request2 = RunRequest(url="http://192.0.2.1", goal="second run check")
    runner2 = await capture_runner.start_run(request2, cfg=cfg)
    assert runner2.run_id != runner.run_id

    # Clean up the second run promptly so the test ends quickly
    if runner2.proc:
        runner2.proc.kill()
        try:
            await asyncio.wait_for(runner2.proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass
    runner2._kill_chromium_zombies()
    capture_runner._REGISTRY.pop(runner2.run_id, None)
