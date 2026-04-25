"""Parent-side capture lifecycle manager.

Owns the subprocess, the WebSocket subscriber set, the kill chain, the
lifecycle-event replay buffer, and the 3-second WS-empty grace window.

Architecture (single capture run):

    POST /api/run
        |
        v
    start_run() -> registry[run_id] = CaptureRunner(...)
        |
        +-- spawn(`python -m aesthesis.browser_agent --run-id ID --url ... `)
        +-- launch _stdout_reader (parses JSONL, dispatches per type)
        +-- launch _stderr_reader (forwards subprocess stderr to backend log)
        +-- launch _wallclock_watcher (D1: SIGKILL after capture_max_wall_s)
        +-- record start_time + child PID for zombie sweep

    On a frame line: decode b64 -> binary -> ws.send_bytes to all subscribers
    On a lifecycle line: stash as last_lifecycle (D32) -> ws.send_json to all
    On stdout EOF: subprocess exited; if no terminal lifecycle was sent, fabricate
                   one and broadcast; remove from registry; cancel watcher

    WS connect/disconnect:
        add_subscriber() -> cancel any pending grace-kill, replay last_lifecycle (D32)
        remove_subscriber() -> if subscribers empty and not completed, arm 3s grace-kill (D27)

    Kill chain (D1 + D26):
        1. capture child PIDs first (psutil child walk while parent still alive)
        2. proc.kill() on the python subprocess
        3. wait up to 5s for it to exit
        4. _kill_chromium_zombies(): kill captured PIDs, then sweep by name+create_time
           (handles the case where Chromium was a grandchild of the dying subprocess)

D19 caps active captures at 1 per backend instance — enforced at start_run().
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
from typing import TYPE_CHECKING

from ..config import AppConfig
from .protocol import RunRequest

if TYPE_CHECKING:
    from fastapi import WebSocket  # noqa: F401 — type-only

log = logging.getLogger(__name__)


# ─── Errors ────────────────────────────────────────────────────────────────


class CaptureInProgressError(RuntimeError):
    """Raised by ``start_run`` when D19's cap=1 is already exhausted."""

    def __init__(self, *, active_run_id: str):
        super().__init__(f"capture already in progress: run_id={active_run_id}")
        self.active_run_id = active_run_id


class UnknownRunError(KeyError):
    """Raised when a caller asks for a run_id we don't know about."""


# ─── Module-level registry (D19 — cap 1 enforced in start_run) ────────────


_REGISTRY: dict[str, "CaptureRunner"] = {}


def get_runner(run_id: str) -> "CaptureRunner | None":
    return _REGISTRY.get(run_id)


def active_count() -> int:
    return len(_REGISTRY)


def list_active() -> list[str]:
    return list(_REGISTRY.keys())


async def start_run(request: RunRequest, *, cfg: AppConfig) -> "CaptureRunner":
    """Spawn a capture run. Returns the registered ``CaptureRunner``.

    Raises ``CaptureInProgressError`` if another run is already active.
    """
    if active_count() >= 1:
        existing = next(iter(_REGISTRY))
        log.warning(
            "capture.start_rejected_in_progress",
            extra={"step": "capture", "active_run_id": existing,
                   "incoming_url": str(request.url)},
        )
        raise CaptureInProgressError(active_run_id=existing)

    run_id = str(uuid.uuid4())
    runner = CaptureRunner(run_id, request, cfg=cfg)
    _REGISTRY[run_id] = runner
    log.info(
        "capture.registry_added",
        extra={"step": "capture", "run_id": run_id,
               "registry_size": active_count()},
    )

    try:
        await runner.start()
    except Exception:
        # Failed to spawn — back the registry out so a retry can proceed.
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
        request: RunRequest,
        *,
        cfg: AppConfig,
    ) -> None:
        self.run_id = run_id
        self.request = request
        self.cfg = cfg
        self.run_dir: Path = cfg.upload_dir / run_id

        self.proc: asyncio.subprocess.Process | None = None
        self.start_time: float = 0.0
        self.captured_pids: set[int] = set()

        # WS fan-out
        self.subscribers: set["WebSocket"] = set()
        self.last_lifecycle: dict | None = None  # D32 — replay on reconnect

        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wallclock_task: asyncio.Task | None = None
        self._grace_kill_task: asyncio.Task | None = None  # D27 — 3s WS-empty kill

        # Bookkeeping
        self.completed: bool = False  # set when capture_complete OR capture_failed seen
        self.exit_code: int | None = None
        self.cookies_file: Path | None = None

    # ─── Public API ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the subprocess and begin lifecycle tasks."""
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Persist cookies to disk (subprocess reads them — keeps argv small)
        if self.request.auth and self.request.auth.cookies:
            self.cookies_file = self.run_dir / "auth_cookies.json"
            self.cookies_file.write_text(
                json.dumps(self.request.auth.model_dump()),
                encoding="utf-8",
            )
            log.info(
                "capture.cookies_persisted",
                extra={"step": "capture", "run_id": self.run_id,
                       "n_cookies": len(self.request.auth.cookies),
                       "path": str(self.cookies_file)},
            )

        argv = [
            sys.executable, "-m", "aesthesis.browser_agent",
            "--url", str(self.request.url),
            "--run-dir", str(self.run_dir),
            "--max-recording-s", str(self.cfg.capture_recording_cap_s),
            "--headless", "1" if self.cfg.chromium_headless else "0",
            "--browseruse-model", self.cfg.browseruse_model,
            "--run-id", self.run_id,
            "--viewport-width", str(self.cfg.capture_viewport_width),
            "--viewport-height", str(self.cfg.capture_viewport_height),
        ]
        if self.request.goal:
            argv += ["--goal", self.request.goal]
        if self.cookies_file:
            argv += ["--auth-cookies-file", str(self.cookies_file)]

        log.info(
            "capture.spawn_begin",
            extra={
                "step": "capture", "run_id": self.run_id,
                "argv": argv,
                "url": str(self.request.url),
                "goal_present": self.request.goal is not None,
                "max_wall_s": self.cfg.capture_max_wall_s,
            },
        )

        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},  # forward GEMINI_API_KEY etc.
            cwd=str(Path(__file__).resolve().parents[2]),  # aesthesis_app dir
        )
        self.start_time = time.monotonic()
        log.info(
            "capture.spawn_done",
            extra={"step": "capture", "run_id": self.run_id,
                   "pid": self.proc.pid},
        )

        self._stdout_task = asyncio.create_task(
            self._stdout_reader(), name=f"capture-stdout-{self.run_id[:8]}",
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_reader(), name=f"capture-stderr-{self.run_id[:8]}",
        )
        self._wallclock_task = asyncio.create_task(
            self._wallclock_watcher(), name=f"capture-wallclock-{self.run_id[:8]}",
        )

    async def add_subscriber(self, ws: "WebSocket") -> None:
        """Register a WS subscriber. Cancels any pending grace-kill (D27)
        and replays ``last_lifecycle`` (D32) so the new subscriber sees
        the latest state immediately."""
        self.subscribers.add(ws)
        log.info(
            "capture.subscriber_added",
            extra={"step": "ws", "run_id": self.run_id,
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
            extra={"step": "ws", "run_id": self.run_id,
                   "n_subscribers": len(self.subscribers)},
        )
        # D27 — arm the 3s grace-kill if no subscribers AND capture still live
        if not self.subscribers and not self.completed:
            log.info(
                "capture.grace_kill_armed",
                extra={"step": "ws", "run_id": self.run_id, "grace_s": 3.0},
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
        except Exception as e:  # noqa: BLE001 — stdout pipe died
            log.error(
                "capture.stdout_reader_crashed: %s",
                e,
                extra={"step": "capture", "run_id": self.run_id},
            )
        finally:
            log.info(
                "capture.stdout_eof",
                extra={"step": "capture", "run_id": self.run_id},
            )
            await self._on_subprocess_exit()

    async def _stderr_reader(self) -> None:
        assert self.proc and self.proc.stderr, "subprocess has no stderr pipe"
        try:
            async for raw in self.proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    # Subprocess logs already include their own structure;
                    # surface them as backend INFO with run_id correlation.
                    log.info(
                        "subprocess.stderr",
                        extra={"step": "subprocess", "run_id": self.run_id,
                               "line": line[:1000]},
                    )
        except Exception as e:  # noqa: BLE001 — stderr pipe death is informational
            log.debug(
                "capture.stderr_reader_ended: %s",
                e,
                extra={"step": "capture", "run_id": self.run_id},
            )

    async def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "frame":
            await self._broadcast_frame(msg)
            return

        if msg_type in ("stream_degraded", "capture_complete",
                        "capture_failed", "agent_event"):
            self.last_lifecycle = msg  # D32
            log.info(
                "capture.lifecycle",
                extra={"step": "capture", "run_id": self.run_id,
                       "type": msg_type, "payload": {k: v for k, v in msg.items() if k != "type"}},
            )
            await self._broadcast_json(msg)
            if msg_type in ("capture_complete", "capture_failed"):
                self.completed = True
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
            log.warning(
                "capture.frame_missing_b64",
                extra={"step": "capture", "run_id": self.run_id},
            )
            return
        try:
            jpeg_bytes = base64.b64decode(b64)
        except Exception as e:  # noqa: BLE001 — corrupt frame
            log.warning(
                "capture.frame_b64_decode_failed: %s",
                e,
                extra={"step": "capture", "run_id": self.run_id},
            )
            return

        # Snapshot the subscriber set so concurrent disconnects don't trip us
        for ws in list(self.subscribers):
            try:
                await ws.send_bytes(jpeg_bytes)
            except Exception as e:  # noqa: BLE001 — single subscriber died
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
        """D1: hard wall-clock timeout. SIGKILL on hit."""
        try:
            await asyncio.sleep(self.cfg.capture_max_wall_s)
        except asyncio.CancelledError:
            return
        if self.completed:
            return
        log.warning(
            "capture.wallclock_timeout_firing_sigkill",
            extra={"step": "capture", "run_id": self.run_id,
                   "wall_s": self.cfg.capture_max_wall_s},
        )
        # Synthesize the lifecycle event since the subprocess won't get to.
        failed = {
            "type": "capture_failed",
            "run_id": self.run_id,
            "reason": "timeout",
            "message": f"wall-clock {self.cfg.capture_max_wall_s}s exceeded; SIGKILL fired",
        }
        self.last_lifecycle = failed
        await self._broadcast_json(failed)
        self.completed = True
        await self._kill_subprocess(reason="timeout")

    async def _grace_kill(self) -> None:
        """D27: wait 3 seconds; if still no subscribers, kill the run."""
        try:
            await asyncio.sleep(3.0)
        except asyncio.CancelledError:
            log.info(
                "capture.grace_kill_disarmed",
                extra={"step": "ws", "run_id": self.run_id},
            )
            return

        if self.subscribers or self.completed:
            return
        log.warning(
            "capture.grace_kill_firing_sigkill",
            extra={"step": "ws", "run_id": self.run_id},
        )
        failed = {
            "type": "capture_failed",
            "run_id": self.run_id,
            "reason": "crashed",
            "message": "all WS subscribers gone for 3s; capture cancelled",
        }
        self.last_lifecycle = failed
        self.completed = True
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
                extra={"step": "kill", "run_id": self.run_id,
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
        except Exception as e:  # noqa: BLE001 — psutil failure shouldn't block
            log.warning(
                "capture.capture_chromium_pids_failed: %s",
                e,
                extra={"step": "kill", "run_id": self.run_id},
            )

    def _kill_chromium_zombies(self) -> None:
        """D26: kill captured PIDs first, then sweep by name with create_time
        bound. Belt-and-suspenders for the case where Chromium was a
        grandchild and its parent already exited (orphaning the PID-tree
        walk above).
        """
        try:
            import psutil
        except ImportError:
            log.error(
                "capture.psutil_missing_cannot_sweep_zombies",
                extra={"step": "kill", "run_id": self.run_id},
            )
            return

        # By PID — the precise list we captured earlier
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

        # By name + recent create_time — catches grandchildren
        n_killed_name = 0
        cutoff_lo = self.start_time + (time.time() - time.monotonic())  # convert monotonic->wall
        names = ("chrome.exe", "chromium.exe", "chrome", "chromium")
        for p in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if pname not in names:
                    continue
                ct = p.info.get("create_time") or 0.0
                # Only kill chromium spawned within ±30s of our start
                if abs(ct - cutoff_lo) > 30:
                    continue
                # Skip if we already killed it via captured_pids
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
                    "capture.zombie_iter_skip: %s",
                    e,
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
            extra={"step": "capture", "run_id": self.run_id,
                   "exit_code": self.exit_code,
                   "had_terminal_lifecycle": self.last_lifecycle is not None
                                              and self.last_lifecycle.get("type") in ("capture_complete", "capture_failed")},
        )

        # Subprocess exited without sending a terminal lifecycle event ->
        # synthesize a capture_failed with crashed reason
        if (self.last_lifecycle is None
                or self.last_lifecycle.get("type") not in ("capture_complete", "capture_failed")):
            failed = {
                "type": "capture_failed",
                "run_id": self.run_id,
                "reason": "crashed",
                "message": (f"subprocess exited rc={self.exit_code} without "
                            f"emitting capture_complete or capture_failed"),
            }
            self.last_lifecycle = failed
            await self._broadcast_json(failed)

        self.completed = True

        # Cancel watchers (in case they are still ticking)
        for t in (self._wallclock_task, self._grace_kill_task):
            if t and not t.done():
                t.cancel()

        # Remove from registry. NOTE: we do NOT touch the subscribers — they
        # remain attached so the WS endpoint can flush the last lifecycle and
        # then close itself. The /api/run/{id}/video and /api/analyze/by-run
        # endpoints continue to work for as long as the run_dir exists.
        _REGISTRY.pop(self.run_id, None)
        log.info(
            "capture.registry_removed",
            extra={"step": "capture", "run_id": self.run_id,
                   "registry_size": active_count()},
        )
