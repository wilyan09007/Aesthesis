"""Adaptive 5-tier CDP screencast streamer (DESIGN.md §4.2b, decision D9).

Subprocess-side. Owns the CDP session for ``Page.startScreencast`` /
``Page.screencastFrame`` / ``Page.screencastFrameAck``, the rolling FPS
window, the tier-walk loop with hysteresis, and the dual-consumer
emission: each JPEG frame goes both to ``sys.stdout`` as a JSONL line
(parent decodes and forwards as binary WS frame per D30c) and into an
in-memory list ``frames_for_mp4`` that ``browser_agent.py`` later feeds
through ffmpeg to produce the H.264 MP4.

Tier ladder is locked to the spec — do NOT externalize to config.

Verbose structured logging at every state transition. No try/except
swallowing failures: CDP errors propagate, the subprocess dies, the
parent observes via stdout EOF + non-zero exit and emits
``capture_failed``. That is the contract.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TierParams:
    """Parameters for one rung of the adaptive ladder."""

    name: str
    width: int
    height: int
    quality: int          # JPEG quality 0-100
    every_nth_frame: int  # CDP samples every Nth frame from the 30fps base
    target_fps: int       # what we expect to see delivered when network is healthy


# DESIGN.md §4.2b. T0 default (good network), T4 floor (must sustain >= 2 fps).
TIERS: list[TierParams] = [
    TierParams("T0", 800, 600, 60, 3,  10),
    TierParams("T1", 640, 480, 50, 3,  10),
    TierParams("T2", 480, 360, 40, 5,   6),
    TierParams("T3", 320, 240, 35, 10,  3),
    TierParams("T4", 240, 180, 25, 15,  2),  # floor — D9 hard requirement
]


@dataclass
class StreamerStats:
    """Lightweight counters for end-of-run telemetry / debug."""

    frames_emitted: int = 0
    tier_walks_down: int = 0
    tier_walks_up: int = 0
    degraded_emissions: int = 0
    final_tier_idx: int = 0
    avg_fps_observed: float = 0.0
    fps_samples: list[float] = field(default_factory=list)


class AdaptiveStreamer:
    """5-tier adaptive screencast forwarder.

    Construction does NOT start anything — call ``await start()`` after
    the CDP session is open and you've registered any other handlers
    you want on it. Stop via ``await stop()`` before closing the page.

    The ``on_lifecycle`` callback (typically a partial that writes to
    stdout) receives JSON-serialisable dicts for ``stream_degraded`` and
    is the only escape hatch the streamer uses for non-frame events.
    Frame events go straight to ``sys.stdout`` to avoid any extra hops
    in the hot path.
    """

    def __init__(
        self,
        cdp_session,
        run_id: str,
        *,
        on_lifecycle: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.cdp = cdp_session
        self.run_id = run_id
        self.on_lifecycle = on_lifecycle

        self.tier_idx: int = 0
        self.frame_times: collections.deque[float] = collections.deque(maxlen=20)
        self.good_streak_started_at: float | None = None
        self.degraded_emitted: bool = False

        # MP4 stash. Each entry is (monotonic_ts_seconds, raw_jpeg_bytes).
        # browser_agent.py drains this at end-of-run.
        self.frames_for_mp4: list[tuple[float, bytes]] = []

        self._adapt_task: asyncio.Task | None = None
        self.stats = StreamerStats()

    # ─── Public API ─────────────────────────────────────────────────

    @property
    def current_tier(self) -> TierParams:
        return TIERS[self.tier_idx]

    async def start(self) -> None:
        """Apply the initial tier and start the screencast + adapt loop."""
        log.info(
            "streamer.start",
            extra={
                "step": "stream", "run_id": self.run_id,
                "tier_idx": self.tier_idx, "tier_name": self.current_tier.name,
            },
        )
        await self._apply_tier()
        self.cdp.on("Page.screencastFrame", self._on_frame_sync)
        self._adapt_task = asyncio.create_task(self._adapt_loop())
        log.debug("streamer.adapt_loop_started", extra={"run_id": self.run_id})

    async def stop(self) -> None:
        """Stop the adapt loop and the screencast.

        Idempotent. Logs final stats. If the CDP session is already gone
        (Chromium died, page closed), we log + continue rather than raise
        — the caller has nothing useful to do at that point.
        """
        self.stats.final_tier_idx = self.tier_idx
        if self.stats.fps_samples:
            self.stats.avg_fps_observed = sum(self.stats.fps_samples) / len(self.stats.fps_samples)
        log.info(
            "streamer.stop",
            extra={
                "step": "stream", "run_id": self.run_id,
                "frames_emitted": self.stats.frames_emitted,
                "tier_walks_down": self.stats.tier_walks_down,
                "tier_walks_up": self.stats.tier_walks_up,
                "degraded_emissions": self.stats.degraded_emissions,
                "final_tier_idx": self.stats.final_tier_idx,
                "avg_fps_observed": round(self.stats.avg_fps_observed, 2),
                "frames_in_mp4_buffer": len(self.frames_for_mp4),
            },
        )

        if self._adapt_task and not self._adapt_task.done():
            self._adapt_task.cancel()
            try:
                await self._adapt_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 — log, continue shutdown
                log.warning(
                    "streamer.adapt_loop_shutdown_error: %s", e,
                    extra={"step": "stream", "run_id": self.run_id},
                )

        try:
            await self.cdp.send("Page.stopScreencast")
            log.debug("streamer.cdp_stopScreencast_ok", extra={"run_id": self.run_id})
        except Exception as e:  # noqa: BLE001 — CDP may already be closed
            log.warning(
                "streamer.cdp_stopScreencast_failed (cdp likely already closed): %s",
                e,
                extra={"step": "stream", "run_id": self.run_id},
            )

    # ─── Internal: tier application ────────────────────────────────

    async def _apply_tier(self) -> None:
        """Stop+start CDP screencast with the current tier's params.

        CDP doesn't allow updating an active screencast in place — to
        change quality/dimensions/everyNthFrame you must stop and start
        again. The brief gap (~50-150ms) shows up as a frame freeze on
        the frontend; acceptable per spec.
        """
        t = self.current_tier
        log.info(
            "streamer.tier_apply",
            extra={
                "step": "stream", "run_id": self.run_id,
                "tier_name": t.name, "tier_idx": self.tier_idx,
                "width": t.width, "height": t.height,
                "quality": t.quality, "every_nth_frame": t.every_nth_frame,
                "target_fps": t.target_fps,
            },
        )
        try:
            await self.cdp.send("Page.stopScreencast")
        except Exception as e:  # noqa: BLE001 — first call has no active stream
            log.debug("streamer.tier_apply_pre_stop_warn: %s", e, extra={"run_id": self.run_id})

        await self.cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": t.quality,
                "maxWidth": t.width,
                "maxHeight": t.height,
                "everyNthFrame": t.every_nth_frame,
            },
        )

    # ─── Internal: per-frame hot path ──────────────────────────────

    def _on_frame_sync(self, params: dict) -> None:
        """CDP fires this synchronously off the Playwright event loop;
        spawn an async task so we can ``await`` the ack send and stay
        non-blocking.
        """
        asyncio.create_task(self._on_frame(params))

    async def _on_frame(self, params: dict) -> None:
        now = time.monotonic()
        self.frame_times.append(now)
        self.stats.frames_emitted += 1

        b64_data: str = params["data"]
        # Stash raw bytes for the MP4 stitch step. Decode once here so
        # ffmpeg-feed code doesn't have to re-decode on the way out.
        try:
            jpeg_bytes = base64.b64decode(b64_data)
        except Exception as e:  # noqa: BLE001 — malformed CDP frame is fatal
            log.error(
                "streamer.frame_decode_failed: %s",
                e,
                extra={"step": "stream", "run_id": self.run_id, "frame_seq": self.stats.frames_emitted},
            )
            raise
        self.frames_for_mp4.append((now, jpeg_bytes))

        # JSONL on stdout. Parent reads line, decodes b64, forwards as
        # binary WS frame to subscribers (D30c). We keep b64 here on the
        # stdout boundary because newline-delimited binary on a pipe is
        # awkward with Python's text-mode line reader; base64 is the
        # boring choice (~33% inflation, fine on localhost).
        line = json.dumps(
            {
                "type": "frame",
                "b64": b64_data,
                "ts_ms": now * 1000.0,
                "tier_idx": self.tier_idx,
                "tier_name": self.current_tier.name,
                "seq": self.stats.frames_emitted,
            },
            separators=(",", ":"),
        )
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

        # Ack so CDP keeps emitting. If this fails, screencast halts and
        # the next adapt-loop tick will measure 0 FPS and walk down.
        await self.cdp.send(
            "Page.screencastFrameAck",
            {"sessionId": params["sessionId"]},
        )

    # ─── Internal: adapt loop ──────────────────────────────────────

    async def _adapt_loop(self) -> None:
        """Every 2s: measure delivered FPS, walk tier up/down with hysteresis."""
        while True:
            await asyncio.sleep(2.0)
            actual_fps = self._measure_fps()
            self.stats.fps_samples.append(actual_fps)
            target = self.current_tier.target_fps

            log.debug(
                "streamer.adapt_tick",
                extra={
                    "step": "stream", "run_id": self.run_id,
                    "actual_fps": round(actual_fps, 2),
                    "target_fps": target,
                    "tier_idx": self.tier_idx,
                    "tier_name": self.current_tier.name,
                    "good_streak_active": self.good_streak_started_at is not None,
                },
            )

            # Walk DOWN: actual < target * 0.8 and we're not at the floor
            if actual_fps < target * 0.8 and self.tier_idx < len(TIERS) - 1:
                old_idx = self.tier_idx
                self.tier_idx += 1
                self.good_streak_started_at = None
                self.stats.tier_walks_down += 1
                log.warning(
                    "streamer.tier_down",
                    extra={
                        "step": "stream", "run_id": self.run_id,
                        "from_idx": old_idx, "to_idx": self.tier_idx,
                        "actual_fps": round(actual_fps, 2),
                        "target_fps": target,
                    },
                )
                await self._apply_tier()

                # If we landed on T4 and STILL can't sustain 2 fps -> degraded
                if self.tier_idx == len(TIERS) - 1 and actual_fps < 2.0 and not self.degraded_emitted:
                    self.degraded_emitted = True
                    self.stats.degraded_emissions += 1
                    log.error(
                        "streamer.degraded_floor_breach",
                        extra={
                            "step": "stream", "run_id": self.run_id,
                            "actual_fps": round(actual_fps, 2),
                        },
                    )
                    await self.on_lifecycle({"type": "stream_degraded"})

            # Walk UP: actual > target * 1.2, hold for 4s, then step
            elif actual_fps > target * 1.2 and self.tier_idx > 0:
                if self.good_streak_started_at is None:
                    self.good_streak_started_at = time.monotonic()
                    log.debug(
                        "streamer.good_streak_started",
                        extra={"step": "stream", "run_id": self.run_id, "actual_fps": round(actual_fps, 2)},
                    )
                elif time.monotonic() - self.good_streak_started_at > 4.0:
                    old_idx = self.tier_idx
                    self.tier_idx -= 1
                    self.good_streak_started_at = None
                    self.stats.tier_walks_up += 1
                    log.info(
                        "streamer.tier_up",
                        extra={
                            "step": "stream", "run_id": self.run_id,
                            "from_idx": old_idx, "to_idx": self.tier_idx,
                            "actual_fps": round(actual_fps, 2),
                        },
                    )
                    await self._apply_tier()
                    # If we left the floor, the degraded flag can be re-armed
                    if self.tier_idx < len(TIERS) - 1:
                        self.degraded_emitted = False

            else:
                # In hysteresis dead-zone — reset any pending good-streak timer
                self.good_streak_started_at = None

    def _measure_fps(self) -> float:
        """Compute observed FPS over the rolling 20-sample window."""
        if len(self.frame_times) < 2:
            return 0.0
        window = self.frame_times[-1] - self.frame_times[0]
        if window <= 0:
            return 0.0
        return len(self.frame_times) / window
