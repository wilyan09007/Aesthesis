"""Parent-side capture lifecycle manager — two-phase (pre-warm + capture).

Owns the subprocess, the WebSocket subscriber set, the kill chain, the
lifecycle-event replay buffer, and the 3-second WS-empty grace window.

Lifecycle phases (``CaptureRunner.phase``):

  spawning  → just exec'd the subprocess
  warming   → subprocess is launching Chromium + CDP screencast + LLM client
  ready     → subprocess emitted prewarm_ready; awaiting stdin start command
  running   → start_capture() was called; subprocess is driving the page
  completed → capture_complete or capture_failed received; cleanup phase

The wall-clock D1 timer (``capture_max_wall_s``) starts ONLY when phase
transitions to ``running`` — pre-warm doesn't count toward the budget
because users may take 30s to type a URL.

Two-phase API:

  start_run(prewarm_only=False)  → spawn subprocess + auto-send start
                                    when prewarm_ready arrives (legacy
                                    /api/run path)
  start_run(prewarm_only=True)   → spawn subprocess; caller will invoke
                                    start_capture() later (new
                                    /api/prewarm + /api/run/{id}/start
                                    flow)
  await runner.start_capture(url, goal, auth)  → send start command via
                                    stdin; transitions phase warming/ready
                                    → running

D26 kill chain + D27 WS-empty grace + D32 last-lifecycle replay all
unchanged — they work the same regardless of phase.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..config import AppConfig
from .protocol import AuthSpec, RunRequest

if TYPE_CHECKING:
    from fastapi import WebSocket  # noqa: F401 — type-only

log = logging.getLogger(__name__)

CapturePhase = Literal["spawning", "warming", "ready", "running", "completed"]


# ─── Errors ────────────────────────────────────────────────────────────────


class CaptureInProgressError(RuntimeError):
    """Raised by ``start_run`` when D19's cap=1 is already exhausted."""

    def __init__(self, *, active_run_id: str):
        super().__init__(f"capture already in progress: run_id={active_run_id}")
        self.active_run_id = active_run_id


class UnknownRunError(KeyError):
    """Raised when a caller asks for a run_id we don't know about."""


class CaptureNotReadyError(RuntimeError):
    """Raised when start_capture is called before the subprocess emits
    prewarm_ready (or after it has already entered running/completed)."""


# ─── Module-level registry (D19 — cap 1 enforced in start_run) ────────────


_REGISTRY: dict[str, "CaptureRunner"] = {}


def get_runner(run_id: str) -> "CaptureRunner | None":
    return _REGISTRY.get(run_id)


def active_count() -> int:
    return len(_REGISTRY)


def list_active() -> list[str]:
    return list(_REGISTRY.keys())


async def start_run(
    request: RunRequest | None,
    *,
    cfg: AppConfig,
    prewarm_only: bool = False,
) -> "CaptureRunner":
    """Spawn a capture subprocess. Returns the registered ``CaptureRunner``.

    - ``prewarm_only=False`` (legacy /api/run): ``request`` MUST be
      provided (URL + goal + optional auth). The runner spawns the
      subprocess in pre-warm mode AND auto-sends the start command as
      soon as prewarm_ready arrives.
    - ``prewarm_only=True`` (new /api/prewarm): ``request`` may be None.
      The runner spawns the subprocess and awaits a manual
      ``start_capture()`` call later.

    Raises ``CaptureInProgressError`` if another run is already active.
    """
    if active_count() >= 1:
        existing = next(iter(_REGISTRY))
        log.warning(
            "capture.start_rejected_in_progress",
            extra={"step": "capture", "active_run_id": existing,
                   "incoming_url": str(request.url) if request else None,
                   "prewarm_only": prewarm_only},
        )
        raise CaptureInProgressError(active_run_id=existing)

    run_id = str(uuid.uuid4())
    runner = CaptureRunner(run_id, cfg=cfg, prewarm_only=prewarm_only,
                           pending_request=request)
    _REGISTRY[run_id] = runner
    log.info(
        "capture.registry_added",
        extra={"step": "capture", "run_id": run_id,
               "registry_size": active_count(),
               "prewarm_only": prewarm_only,
               "has_request": request is not None},
    )

    try:
        await runner.start()
    except Exception:
        _REGISTRY.pop(run_id, None)
        log.exception(
            "capture.spawn_failed_rolling_back_registry",
            extra={"step": "capture", "run_id": run_id},
        )
        raise
    return runner


# ─── Capture runner ────────────────────────────────────────────────────────


class CaptureRunner:
    """Owns one capture run end-to-end. Singleton-per-run_id."""

    def __init__(
        self,
        run_id: str,
        *,
        cfg: AppConfig,
        prewarm_only: bool = False,
        pending_request: RunRequest | None = None,
    ) -> None:
        self.run_id = run_id
        self.cfg = cfg
        self.run_dir: Path = cfg.upload_dir / run_id

        # ── Lifecycle state ────────────────────────────────────────
        self.phase: CapturePhase = "spawning"
        self.start_time: float = 0.0  # set when subprocess spawns
        self.run_started_time: float = 0.0  # set when phase -> running
        self.completed: bool = False
        self.exit_code: int | None = None

        # ── Subprocess + tasks ─────────────────────────────────────
        self.proc: asyncio.subprocess.Process | None = None
        self.captured_pids: set[int] = set()

        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wallclock_task: asyncio.Task | None = None  # only spawned in transition_to_running
        self._grace_kill_task: asyncio.Task | None = None  # D27

        # ── WS fan-out ─────────────────────────────────────────────
        self.subscribers: set["WebSocket"] = set()
        self.last_lifecycle: dict | None = None  # D32

        # ── Pre-warm bookkeeping ───────────────────────────────────
        self.prewarm_only: bool = prewarm_only
        self.pending_request: RunRequest | None = pending_request
        # Event that fires when prewarm_ready arrives — used by start_capture()
        # callers that want to await the ready signal before issuing start.
        self._prewarm_ready_event: asyncio.Event = asyncio.Event()

    # ─── Public API ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the subprocess and begin lifecycle tasks."""
        self.run_dir.mkdir(parents=True, exist_ok=True)

        argv = [
            sys.executable, "-m", "aesthesis.browser_agent",
            "--run-dir", str(self.run_dir),
            "--run-id", self.run_id,
            "--max-recording-s", str(self.cfg.capture_recording_cap_s),
            "--headless", "1" if self.cfg.chromium_headless else "0",
            "--browseruse-model", self.cfg.browseruse_model,
            "--viewport-width", str(self.cfg.capture_viewport_width),
            "--viewport-height", str(self.cfg.capture_viewport_height),
        ]

        log.info(
            "capture.spawn_begin",
            extra={
                "step": "capture", "run_id": self.run_id,
                "phase": self.phase,
                "argv": argv,
                "prewarm_only": self.prewarm_only,
                "max_wall_s": self.cfg.capture_max_wall_s,
            },
        )

        # We need a writable stdin so we can send the start command later
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},  # forward GEMINI_API_KEY etc.
            cwd=str(Path(__file__).resolve().parents[2]),  # aesthesis_app dir
        )
        self.start_time = time.monotonic()
        self.phase = "warming"
        log.info(
            "capture.spawn_done",
            extra={"step": "capture", "run_id": self.run_id,
                   "phase": self.phase, "pid": self.proc.pid},
        )

        self._stdout_task = asyncio.create_task(
            self._stdout_reader(), name=f"capture-stdout-{self.run_id[:8]}",
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_reader(), name=f"capture-stderr-{self.run_id[:8]}",
        )
        # Wall-clock task is NOT spawned here — only after phase -> running.

    async def start_capture(self, url: str, goal: str | None,
                            auth: AuthSpec | None) -> None:
        """Send the stdin start command, transition phase warming/ready
        → running, and arm the wall-clock timer.

        Idempotent: calling twice on a run that's already running is a
        no-op (logs a warning). Calling on a completed run raises.
        """
        if self.phase in ("running", "completed"):
            log.warning(
                "capture.start_capture_ignored_already_advanced",
                extra={"step": "capture", "run_id": self.run_id, "phase": self.phase},
            )
            if self.phase == "completed":
                raise CaptureNotReadyError(
                    f"start_capture called on completed run {self.run_id}"
                )
            return

        # Wait for prewarm_ready (with a generous 60s timeout — Chromium
        # launch + CDP setup + LLM init shouldn't take longer)
        if not self._prewarm_ready_event.is_set():
            log.info(
                "capture.start_capture_awaiting_prewarm_ready",
                extra={"step": "capture", "run_id": self.run_id, "phase": self.phase},
            )
            try:
                await asyncio.wait_for(self._prewarm_ready_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                raise CaptureNotReadyError(
                    f"timed out waiting for prewarm_ready on run {self.run_id}"
                )

        if not self.proc or not self.proc.stdin:
            raise CaptureNotReadyError(
                f"subprocess for run {self.run_id} has no stdin (already exited?)"
            )

        # Compose + send the start command
        cmd = {
            "type": "start",
            "url": str(url),
            "goal": goal,
        }
        if auth is not None:
            cmd["auth"] = auth.model_dump()
        line = json.dumps(cmd, separators=(",", ":")) + "\n"

        log.info(
            "capture.sending_start_command",
            extra={"step": "capture", "run_id": self.run_id, "phase": self.phase,
                   "url": str(url), "goal_present": goal is not None,
                   "n_cookies": len(auth.cookies) if (auth and auth.cookies) else 0},
        )

        try:
            self.proc.stdin.write(line.encode("utf-8"))
            await self.proc.stdin.drain()
            # Closing stdin signals "no more commands" to the subprocess
            self.proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise CaptureNotReadyError(
                f"could not send start command to run {self.run_id}: {e}"
            )

        # Transition phase + arm wall-clock D1 timer
        self.phase = "running"
        self.run_started_time = time.monotonic()
        self._wallclock_task = asyncio.create_task(
            self._wallclock_watcher(),
            name=f"capture-wallclock-{self.run_id[:8]}",
        )
        log.info(
            "capture.transitioned_to_running",
            extra={"step": "capture", "run_id": self.run_id, "phase": self.phase,
                   "wallclock_s": self.cfg.capture_max_wall_s},
        )

    async def add_subscriber(self, ws: "WebSocket") -> None:
        """Register a WS subscriber. Cancels any pending grace-kill (D27)
        and replays ``last_lifecycle`` (D32) so the new subscriber sees
        the latest state immediately."""
        self.subscribers.add(ws)
        log.info(
            "capture.subscriber_added",
            extra={"step": "ws", "run_id": self.run_id, "phase": self.phase,
                   "n_subscribers": len(self.subscribers)},
        )

        # D27 — subscriber returned within grace window; abort the kill
        if self._grace_kill_task and not self._grace_kill_task.done():
            self._grace_kill_task.cancel()
            log.info(
                "capture.grace_kill_disarmed_subscriber_returned",
                extra={"step": "ws", "run_id": self.run_id},
            )
            self._grace_kill_task = None

        # D32 — replay last lifecycle event if any
        if self.last_lifecycle is not None:
            log.info(
                "capture.lifecycle_replay",
                extra={"step": "ws", "run_id": self.run_id,
                       "type": self.last_lifecycle.get("type")},
            )
            with contextlib.suppress(Exception):
                await ws.send_json(self.last_lifecycle)

    async def remove_subscriber(self, ws: "WebSocket") -> None:
        self.subscribers.discard(ws)
        log.info(
            "capture.subscriber_removed",
            extra={"step": "ws", "run_id": self.run_id, "phase": self.phase,
                   "n_subscribers": len(self.subscribers)},
        )
        if not self.subscribers and not self.completed:
            log.info(
                "capture.grace_kill_armed",
                extra={"step": "ws", "run_id": self.run_id, "grace_s": 3.0,
                       "phase": self.phase},
            )
            self._grace_kill_task = asyncio.create_task(
                self._grace_kill(), name=f"capture-grace-{self.run_id[:8]}",
            )

    # ─── Stdout protocol parser + dispatcher ────────────────────────

    async def _stdout_reader(self) -> None:
        assert self.proc and self.proc.stdout, "subprocess has no stdout pipe"
        log.debug(
            "capture.stdout_reader_started",
            extra={"step": "capture", "run_id": self.run_id},
        )
        try:
            async for raw in self.proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning(
                        "capture.stdout_garbage",
                        extra={"step": "capture", "run_id": self.run_id,
                               "snippet": line[:200], "error": str(e)},
                    )
                    continue
                await self._dispatch(msg)
        except Exception as e:  # noqa: BLE001
            log.error(
                "capture.stdout_reader_crashed: %s", e,
                extra={"step": "capture", "run_id": self.run_id},
            )
        finally:
            log.info(
                "capture.stdout_eof",
                extra={"step": "capture", "run_id": self.run_id, "phase": self.phase},
            )
            await self._on_subprocess_exit()

    async def _stderr_reader(self) -> None:
        assert self.proc and self.proc.stderr, "subprocess has no stderr pipe"
        try:
            async for raw in self.proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    log.info(
                        "subprocess.stderr",
                        extra={"step": "subprocess", "run_id": self.run_id,
                               "phase": self.phase, "line": line[:1000]},
                    )
        except Exception as e:  # noqa: BLE001
            log.debug(
                "capture.stderr_reader_ended: %s", e,
                extra={"step": "capture", "run_id": self.run_id},
            )

    async def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "frame":
            await self._broadcast_frame(msg)
            return

        if msg_type == "prewarm_ready":
            # Phase transition: warming -> ready
            if self.phase == "warming":
                self.phase = "ready"
                log.info(
                    "capture.prewarm_ready",
                    extra={"step": "capture", "run_id": self.run_id,
                           "phase": self.phase,
                           "elapsed_s": round(time.monotonic() - self.start_time, 2),
                           "cdp_port": msg.get("cdp_port")},
                )
            self._prewarm_ready_event.set()
            self.last_lifecycle = msg  # D32 — replay on reconnect
            await self._broadcast_json(msg)
            # If we're in legacy /api/run mode, auto-send the start command
            if not self.prewarm_only and self.pending_request is not None:
                log.info(
                    "capture.auto_start_after_prewarm",
                    extra={"step": "capture", "run_id": self.run_id,
                           "url": str(self.pending_request.url)},
                )
                try:
                    await self.start_capture(
                        url=str(self.pending_request.url),
                        goal=self.pending_request.goal,
                        auth=self.pending_request.auth,
                    )
                except Exception as e:  # noqa: BLE001
                    log.error(
                        "capture.auto_start_failed: %s", e,
                        extra={"step": "capture", "run_id": self.run_id},
                    )
            return

        if msg_type in ("stream_degraded", "capture_complete",
                        "capture_failed", "agent_event"):
            self.last_lifecycle = msg  # D32
            log.info(
                "capture.lifecycle",
                extra={"step": "capture", "run_id": self.run_id, "phase": self.phase,
                       "type": msg_type,
                       "payload": {k: v for k, v in msg.items() if k != "type"}},
            )
            await self._broadcast_json(msg)
            if msg_type in ("capture_complete", "capture_failed"):
                self.completed = True
                self.phase = "completed"
            return

        log.warning(
            "capture.unknown_msg_type",
            extra={"step": "capture", "run_id": self.run_id, "type": msg_type,
                   "snippet": json.dumps(msg)[:200]},
        )

    async def _broadcast_frame(self, msg: dict) -> None:
        """Decode b64 -> binary, send to all subscribers as binary WS frame.

        D30c: binary frames go on the WS wire; control messages stay JSON.
        """
        b64 = msg.get("b64", "")
        if not b64:
            return
        try:
            jpeg_bytes = base64.b64decode(b64)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "capture.frame_b64_decode_failed: %s", e,
                extra={"step": "capture", "run_id": self.run_id},
            )
            return

        for ws in list(self.subscribers):
            try:
                await ws.send_bytes(jpeg_bytes)
            except Exception as e:  # noqa: BLE001
                log.info(
                    "capture.subscriber_send_bytes_failed_dropping",
                    extra={"step": "ws", "run_id": self.run_id, "error": str(e)},
                )
                self.subscribers.discard(ws)

    async def _broadcast_json(self, msg: dict) -> None:
        for ws in list(self.subscribers):
            try:
                await ws.send_json(msg)
            except Exception as e:  # noqa: BLE001
                log.info(
                    "capture.subscriber_send_json_failed_dropping",
                    extra={"step": "ws", "run_id": self.run_id, "error": str(e)},
                )
                self.subscribers.discard(ws)

    # ─── Lifecycle: timeout + grace-kill + zombie sweep ─────────────

    async def _wallclock_watcher(self) -> None:
        """D1: hard wall-clock timeout, ARMED ONLY AFTER phase -> running.

        Pre-warm time doesn't count. The user can take 30s to type a URL
        without burning the capture budget.
        """
        try:
            await asyncio.sleep(self.cfg.capture_max_wall_s)
        except asyncio.CancelledError:
            return
        if self.completed:
            return
        log.warning(
            "capture.wallclock_timeout_firing_sigkill",
            extra={"step": "capture", "run_id": self.run_id, "phase": self.phase,
                   "wall_s": self.cfg.capture_max_wall_s,
                   "elapsed_since_running": round(time.monotonic() - self.run_started_time, 2)},
        )
        failed = {
            "type": "capture_failed",
            "run_id": self.run_id,
            "reason": "timeout",
            "message": (f"wall-clock {self.cfg.capture_max_wall_s}s exceeded "
                        f"after start; SIGKILL fired"),
        }
        self.last_lifecycle = failed
        await self._broadcast_json(failed)
        self.completed = True
        self.phase = "completed"
        await self._kill_subprocess(reason="timeout")

    async def _grace_kill(self) -> None:
        """D27: wait 3 seconds; if still no subscribers, kill the run.

        Applies regardless of phase — also kills idle pre-warm sessions
        if the user navigates away from /capture without clicking Start.
        """
        try:
            await asyncio.sleep(3.0)
        except asyncio.CancelledError:
            log.info(
                "capture.grace_kill_disarmed",
                extra={"step": "ws", "run_id": self.run_id, "phase": self.phase},
            )
            return

        if self.subscribers or self.completed:
            return
        log.warning(
            "capture.grace_kill_firing_sigkill",
            extra={"step": "ws", "run_id": self.run_id, "phase": self.phase},
        )
        failed = {
            "type": "capture_failed",
            "run_id": self.run_id,
            "reason": "crashed",
            "message": (f"all WS subscribers gone for 3s in phase {self.phase}; "
                        "run cancelled"),
        }
        self.last_lifecycle = failed
        self.completed = True
        self.phase = "completed"
        await self._kill_subprocess(reason="cancel")

    async def _kill_subprocess(self, *, reason: str) -> None:
        if not self.proc:
            return
        # Grab child PIDs FIRST — once we kill the parent they may orphan.
        self._capture_chromium_pids()
        try:
            self.proc.kill()
            log.info(
                "capture.proc_kill_signal_sent",
                extra={"step": "kill", "run_id": self.run_id, "phase": self.phase,
                       "pid": self.proc.pid, "reason": reason},
            )
        except ProcessLookupError:
            log.debug(
                "capture.proc_already_dead_when_killing",
                extra={"step": "kill", "run_id": self.run_id, "pid": self.proc.pid},
            )

        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            log.info(
                "capture.proc_exited_after_kill",
                extra={"step": "kill", "run_id": self.run_id,
                       "rc": self.proc.returncode},
            )
        except asyncio.TimeoutError:
            log.error(
                "capture.proc_did_not_exit_in_5s_after_kill",
                extra={"step": "kill", "run_id": self.run_id, "pid": self.proc.pid},
            )

        self._kill_chromium_zombies()

    def _capture_chromium_pids(self) -> None:
        """Walk the process tree NOW and remember any chromium descendants."""
        if not self.proc:
            return
        try:
            import psutil
            try:
                parent = psutil.Process(self.proc.pid)
            except psutil.NoSuchProcess:
                log.debug(
                    "capture.parent_already_gone_skipping_walk",
                    extra={"step": "kill", "run_id": self.run_id},
                )
                return
            for child in parent.children(recursive=True):
                try:
                    name = (child.name() or "").lower()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                if any(n in name for n in ("chrome", "chromium")):
                    self.captured_pids.add(child.pid)
            log.info(
                "capture.captured_chromium_pids",
                extra={"step": "kill", "run_id": self.run_id,
                       "n_pids": len(self.captured_pids),
                       "pids": list(self.captured_pids)[:20]},
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "capture.capture_chromium_pids_failed: %s", e,
                extra={"step": "kill", "run_id": self.run_id},
            )

    def _kill_chromium_zombies(self) -> None:
        """D26: kill captured PIDs first, then sweep by name with create_time
        bound. Belt-and-suspenders for the case where Chromium was a
        grandchild and its parent already exited.
        """
        try:
            import psutil
        except ImportError:
            log.error(
                "capture.psutil_missing_cannot_sweep_zombies",
                extra={"step": "kill", "run_id": self.run_id},
            )
            return

        n_killed_pid = 0
        for pid in self.captured_pids:
            try:
                p = psutil.Process(pid)
                p.kill()
                n_killed_pid += 1
            except psutil.NoSuchProcess:
                continue
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "capture.zombie_kill_pid_failed",
                    extra={"step": "kill", "run_id": self.run_id,
                           "pid": pid, "error": str(e)},
                )

        n_killed_name = 0
        cutoff_lo = self.start_time + (time.time() - time.monotonic())
        names = ("chrome.exe", "chromium.exe", "chrome", "chromium")
        for p in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if pname not in names:
                    continue
                ct = p.info.get("create_time") or 0.0
                if abs(ct - cutoff_lo) > 30:
                    continue
                if p.pid in self.captured_pids:
                    continue
                p.kill()
                n_killed_name += 1
                log.warning(
                    "capture.zombie_killed_by_name",
                    extra={"step": "kill", "run_id": self.run_id,
                           "pid": p.pid, "name": pname, "create_time": ct},
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:  # noqa: BLE001
                log.debug(
                    "capture.zombie_iter_skip: %s", e,
                    extra={"step": "kill", "run_id": self.run_id},
                )

        log.info(
            "capture.zombie_sweep_done",
            extra={"step": "kill", "run_id": self.run_id,
                   "n_killed_by_pid": n_killed_pid,
                   "n_killed_by_name": n_killed_name},
        )

    # ─── Subprocess exit finalize ───────────────────────────────────

    async def _on_subprocess_exit(self) -> None:
        if self.proc:
            try:
                self.exit_code = await self.proc.wait()
            except Exception:  # noqa: BLE001
                self.exit_code = -1
        log.info(
            "capture.subprocess_exited",
            extra={"step": "capture", "run_id": self.run_id, "phase": self.phase,
                   "exit_code": self.exit_code,
                   "had_terminal_lifecycle": self.last_lifecycle is not None
                                              and self.last_lifecycle.get("type") in
                                                  ("capture_complete", "capture_failed")},
        )

        if (self.last_lifecycle is None
                or self.last_lifecycle.get("type") not in
                    ("capture_complete", "capture_failed")):
            failed = {
                "type": "capture_failed",
                "run_id": self.run_id,
                "reason": "crashed",
                "message": (f"subprocess exited rc={self.exit_code} in phase "
                            f"{self.phase} without emitting a terminal lifecycle"),
            }
            self.last_lifecycle = failed
            await self._broadcast_json(failed)

        self.completed = True
        self.phase = "completed"

        for t in (self._wallclock_task, self._grace_kill_task):
            if t and not t.done():
                t.cancel()

        _REGISTRY.pop(self.run_id, None)
        log.info(
            "capture.registry_removed",
            extra={"step": "capture", "run_id": self.run_id,
                   "registry_size": active_count()},
        )
