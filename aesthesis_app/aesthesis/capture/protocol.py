"""Wire types for the capture pipeline.

Two surfaces:

1. **HTTP request bodies** — what the frontend POSTs to backend endpoints.
   ``RunRequest`` carries the URL + optional goal + optional cookies.
   ``AnalyzeByRunRequest`` carries an optional goal override.

2. **WebSocket messages** — the subprocess emits JSONL on stdout, the
   parent forwards control messages as JSON and frame payloads as binary
   WS frames per D30c. The Pydantic models below define the JSON
   control-message shapes; binary frame bytes have no envelope.

D31: ``AuthSpec`` is cookies-only. Username/password were proposed in D28
but cut to avoid LLM credential injection; future user/pass support
will arrive via Playwright form-fill at the backend boundary, not
through this schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


# ─── Auth ──────────────────────────────────────────────────────────────────


class CookieSpec(BaseModel):
    """A single cookie injected before BrowserUse runs.

    Mirrors Playwright's ``BrowserContext.add_cookies`` payload shape so
    the subprocess can pass them through unmodified. ``domain`` is
    required; ``path`` defaults to ``"/"`` per the cookie spec.
    """

    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float | None = None
    httpOnly: bool | None = None
    secure: bool | None = None
    sameSite: Literal["Strict", "Lax", "None"] | None = None


class AuthSpec(BaseModel):
    """Optional auth context attached to a capture run. Cookies-only per D31."""

    cookies: list[CookieSpec] | None = None


# ─── HTTP request bodies ───────────────────────────────────────────────────


class RunRequest(BaseModel):
    """Body of ``POST /api/run`` — start a capture.

    ``url`` is the page BrowserUse will drive. ``goal`` parameterises the
    BrowserUse system prompt (D8) — when ``None``, the agent gets a
    generic "first-time visitor" task. ``auth`` injects cookies before
    navigation begins.
    """

    url: HttpUrl
    goal: str | None = None
    auth: AuthSpec | None = None


class AnalyzeByRunRequest(BaseModel):
    """Body of ``POST /api/analyze/by-run/{run_id}`` (D11).

    The captured MP4 lives at ``cfg.upload_dir/{run_id}/video.mp4`` from
    the prior ``POST /api/run``. This call kicks off the existing TRIBE
    + Gemini pipeline against that file. ``goal`` lets the caller
    override the original capture-time goal.
    """

    goal: str | None = None


class PrewarmRequest(BaseModel):
    """Body of ``POST /api/prewarm`` (Phase 2 pre-warm protocol).

    Empty body — pre-warming spawns a stand-by subprocess with no URL
    or goal yet. The actual capture is triggered later via
    ``POST /api/run/{run_id}/start`` once the user has filled the form.
    """

    # Reserved for future use (e.g. cookies for the eventual session)
    pass


class StartCaptureRequest(BaseModel):
    """Body of ``POST /api/run/{run_id}/start``.

    Triggers a pre-warmed subprocess to begin its actual capture.
    ``url`` and ``goal`` are forwarded to the subprocess via stdin
    (one JSON line). ``auth.cookies`` (if present) is also sent in
    that same line — the subprocess sets cookies BEFORE navigating.
    """

    url: HttpUrl
    goal: str | None = None
    auth: AuthSpec | None = None


# ─── HTTP response bodies ──────────────────────────────────────────────────


class RunStartedResponse(BaseModel):
    """Returned from ``POST /api/run`` — capture is running in the background.

    The frontend connects to ``ws://.../api/stream/{run_id}`` to receive
    live frames + lifecycle events.
    """

    run_id: str
    status: Literal["started"] = "started"


class CachedDemoEntry(BaseModel):
    """One row in ``GET /api/cached-demos`` (D29 fallback)."""

    url: str
    label: str
    mp4_filename: str  # name within cfg.cached_demos_dir


# ─── WebSocket control-message shapes ──────────────────────────────────────
#
# Binary WS frames carry raw JPEG bytes — no envelope. Anything sent as a
# JSON message is a control message and matches one of the models below.
# D30c.


class WSPrewarmReady(BaseModel):
    """Emitted by the subprocess once pre-warm is done: Chromium launched,
    CDP screencast started, ChatGoogle LLM client constructed, page open
    on a stand-by HTML doc. The frontend can now enable the Start button —
    the user-perceived latency on click-to-first-frame becomes ~0ms.

    Backend's ``CaptureRunner`` flips ``phase`` from ``warming`` to
    ``ready`` when this fires. The wall-clock D1 timer is NOT yet
    running — it starts when the user actually triggers the capture
    via ``POST /api/run/{run_id}/start``.
    """

    type: Literal["prewarm_ready"] = "prewarm_ready"
    run_id: str
    cdp_port: int


class WSStreamDegraded(BaseModel):
    """Emitted when even the floor tier (T4) cannot sustain 2 fps.

    The capture continues server-side regardless — this is a UX hint
    for the frontend to show a "stream degraded" overlay.
    """

    type: Literal["stream_degraded"] = "stream_degraded"


class WSAgentEvent(BaseModel):
    """Optional info event — agent action narration. Off by default in
    v1.1 but the channel is reserved so the frontend can ignore unknown
    types forward-compatibly.
    """

    type: Literal["agent_event"] = "agent_event"
    description: str
    timestamp_ms: float


class WSCaptureComplete(BaseModel):
    """Emitted once the MP4 is finalised on disk.

    Frontend handler: fetch ``GET /api/run/{run_id}/video`` as a Blob,
    transition to AnalyzingView with the 3s confirm countdown (D11),
    then ``POST /api/analyze/by-run/{run_id}``.
    """

    type: Literal["capture_complete"] = "capture_complete"
    run_id: str
    duration_s: float
    mp4_size_bytes: int
    n_actions: int


class WSCaptureFailed(BaseModel):
    """Sad-path lifecycle event. ``reason`` is one of:

    - ``timeout`` — parent SIGKILLed after ``cfg.capture_max_wall_s``
    - ``crashed`` — subprocess exited non-zero (Chromium died, Playwright
      raised, etc.)
    - ``navigation_error`` — BrowserUse hit a 404, login wall, or other
      structural page failure and stopped cleanly
    - ``setup_error`` — pre-launch issue (Chromium binary missing,
      ffmpeg missing, port unavailable, ...)
    """

    type: Literal["capture_failed"] = "capture_failed"
    run_id: str
    reason: Literal["timeout", "crashed", "navigation_error", "setup_error"]
    message: str


# Convenience union — backend ``last_lifecycle`` field types as one of these.
WSLifecycleMessage = WSStreamDegraded | WSCaptureComplete | WSCaptureFailed
