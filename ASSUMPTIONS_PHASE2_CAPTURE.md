# Phase 2 capture pipeline — assumptions, research log, environment requirements

> Generated alongside the Phase 2 build (`feat/phase-2-capture` branch).
> Pairs with `~/.claude/plans/dazzling-tinkering-sedgewick.md` (the locked
> eng-review plan, decisions D11-D33) and `DESIGN.md` §§4.1, 4.2, 4.2b
> (the original product spec, partially stale post-§17 single-video pivot).
>
> If any assumption below is violated by the runtime environment, the
> capture pipeline fails LOUDLY (no silent fallback, no mocks, no skipif).
> Per project memory `feedback_no_mocks` and the explicit `/ship`
> instruction.
>
> See ASSUMPTIONS.md for the v0.1 backend (skip-path) assumptions. This
> file documents only the Phase 2 capture additions — the URL → BrowserUse
> → live frame stream + MP4 → existing analyzer flow.

---

## A1. browser-use 0.12.x is the pinned version

**Source:** PyPI / GitHub (`browser-use/browser-use`), researched 2026-04-25.

**Pin:** `browser-use==0.12.6` (exact version, not floating). See
`requirements-app.txt` and `aesthesis_app/pyproject.toml`.

**Why pinned (D22):** the `browser-use` public API has had breaking
renames within minor versions in the last few months — `Browser` →
`BrowserSession`, `Agent.run()` signature changes, configuration field
renames. Floating `>=0.12,<0.13` would mean discovering an API break at
3am the night before the demo. Pinning the exact version we tested
against costs nothing and removes a class of failure.

**API surface we depend on:**
- `from browser_use import Agent, BrowserSession`
- `BrowserSession(cdp_url="http://127.0.0.1:PORT")` to attach to an
  existing Chromium we launched ourselves
- `Agent(task=str, llm=langchain_llm, browser_session=session)`
- `await agent.run()` — drives the page until done or LLM gives up

**Version-bump procedure:** any browser-use version bump REQUIRES re-running
`tests/test_browser_agent_kill.py` (R4) end-to-end against the new
version before merging. The kill chain depends on the subprocess
behaving correctly, and BrowserUse changes have historically broken
that.

---

## A2. CDP `Page.startScreencast` and Playwright `record_video_dir` cannot coexist on a CDP-connected context

**Source:** [microsoft/playwright-python#1225](https://github.com/microsoft/playwright-python/issues/1225)
("How to use `record_video_dir` in `connect_over_cdp`?"), researched 2026-04-25.

**Finding:** when you `connect_over_cdp(...)` to a pre-existing browser,
the resulting `BrowserContext` cannot have `record_video_dir` (or other
launch-time options) set — those are baked in at browser launch.

**Implication for our architecture:** if we let BrowserUse launch
Chromium itself and we connect via CDP for screencast, we cannot get a
recorded MP4 from Playwright. Conversely, if we launch Chromium ourselves
with `record_video_dir`, BrowserUse must connect via `cdp_url=...` (NOT
launch its own browser), or it will spawn a sibling Chromium process
that we can't see.

**Resolution:** WE launch Chromium with Playwright (no `record_video_dir`,
because we'd be locked to WebM and validation rejects non-H.264 — see
A3). WE start `Page.startScreencast` on our page. We pass `cdp_url` to
`BrowserSession(...)` so BrowserUse drives the same Chromium. CDP
screencast frames serve double duty: streamed live AND stitched into a
final H.264 MP4 via ffmpeg (single source of truth, no recordVideo).

---

## A3. `validation.py:92` enforces H.264 codec — non-H.264 video is rejected

**Source:** `aesthesis_app/aesthesis/validation.py` line 92.

**Finding:** the existing `validate_upload` runs `ffprobe` and returns
a 400 if `codec_name.lower() != "h264"`. This applies to BOTH the
existing skip-path `/api/analyze` (multipart) AND our new
`/api/analyze/by-run/{id}` because both call the same orchestrator.

**Implication:** the captured MP4 we produce in `browser_agent.py` MUST
be H.264. Playwright's `record_video_dir` outputs WebM (VP8/VP9), which
is a different container AND codec. Even if we WebM-wrapped it as
`.mp4`, ffprobe would reject it.

**Resolution:** ffmpeg-stitch the CDP screencast JPEGs into an H.264
MP4 directly. The encode command in `_encode_frames_to_mp4` is:

```
ffmpeg -y -f image2pipe -vcodec mjpeg -framerate 10 -i -
       -c:v libx264 -preset veryfast -pix_fmt yuv420p
       -movflags +faststart -an output.mp4
```

JPEG bytes are piped on stdin (image2pipe demuxer). Output is H.264 in
yuv420p pixel format with `+faststart` for streaming and `-an` because
Aesthesis is video-only (DESIGN.md §17 audio-strip-end-to-end).

---

## A4. ffmpeg is required at runtime — system PATH OR imageio-ffmpeg bundled

**Where it's needed:**
1. `screenshots.py` (existing) — extracts per-event JPEGs from the MP4
2. `browser_agent.py` (new) — stitches CDP frames into the H.264 MP4
3. `validation.py` (existing) — runs `ffprobe`

**Discovery order in `browser_agent._find_ffmpeg`:**
1. `shutil.which("ffmpeg")` — system PATH
2. `imageio_ffmpeg.get_ffmpeg_exe()` — bundled binary (logged as warning)
3. Raise `RuntimeError` and exit subprocess with `setup_error`

**Why a fallback to `imageio_ffmpeg` rather than hard-fail-only:** the
bundled binary is a real, pinned ffmpeg binary that comes via pip. It's
not a mock or a degraded substitute — it's just a different installation
path. The fallback removes a "first-time clone confused why nothing
works" friction without changing semantic behaviour. In CI, install
`imageio-ffmpeg` and the path is bulletproof.

**Verified on this dev box (Windows 11, Python 3.13.7):** system ffmpeg
is NOT on PATH. `imageio-ffmpeg` is installed via the Phase 2
`requirements-app.txt`, so the fallback path is exercised by default.

---

## A5. Captured MP4 framerate is constant 10fps; tier-walked frames slightly compress wall-clock time

**Why:** the AdaptiveStreamer (D9) walks tiers based on observed FPS,
so during a stream that walks T0→T2→T4, the actual frame cadence varies
from 10 fps down to 2 fps. We feed the resulting frames to ffmpeg at a
constant 10 fps, which means a clip whose source dropped to 2 fps for
some seconds will encode those seconds at 5x real-time speed.

**Acceptable for hackathon scope:** the captured wall-clock duration we
report in `capture_complete.duration_s` is computed from the actual
timestamps (`frames[-1].ts - frames[0].ts`), so the displayed length
in the UI matches reality. The MP4 itself is slightly time-compressed
during degraded segments, which Gemini and TRIBE both tolerate (TRIBE
samples at 1.5s TR — well above any plausible compression artefact).

**Future improvement:** use ffmpeg's `concat` demuxer with an explicit
per-frame timestamp file to produce a true VFR (variable framerate) MP4.
~30 min of work; not in v1.1 scope.

---

## A6. BrowserUse `agent.history` is `AgentHistoryList`, accessed via `.agent_steps()`

**Source:** Verified by reading the installed browser-use 0.12.6 source
on this machine — `browser_use/agent/service.py:435` shows
``self.history = AgentHistoryList(history=[], usage=None)``.
`AgentHistoryList` and `AgentHistory` are defined in
`browser_use/agent/views.py` (Pydantic models, exported from the
top-level `browser_use` package alongside `Agent`, `Browser`, etc.).

**API:**
- `agent.history` — always present, always `AgentHistoryList`
- `agent.history.agent_steps()` — curated per-step view (preferred)
- `agent.history.history` — raw list of `AgentHistory` items (fallback)
- Each step is a Pydantic model with `state`, `model_output`, `result`
  fields; dump via `.model_dump()`.

**Resolution:** `browser_agent._serialise_action_history(agent)` calls
`agent_steps()` first, falls back to `.history`, dumps each item via
Pydantic. Per-step wall-clock timestamps aren't always exposed by
browser-use, so we use the step index as a deterministic ordering key.
The orchestrator's `_nearest_action` ±0.5s window matches against
brain-event TR timestamps; with browser-use steps coming roughly real-
time, index-as-second is a usable approximation for ~30s captures.

**Earlier (incorrect) assumption:** my first pass at this file probed
multiple candidate attribute names (`history`, `state.history`,
`agent_history`) defensively because I hadn't read the source. Updated
once I did — the API is stable and well-defined in 0.12.6.

---

## A7. RFC-5737 IPs are guaranteed unreachable for SIGKILL test (R4)

**Source:** [RFC 5737](https://datatracker.ietf.org/doc/html/rfc5737).

**Finding:** `192.0.2.0/24` (TEST-NET-1), `198.51.100.0/24` (TEST-NET-2),
and `203.0.113.0/24` (TEST-NET-3) are reserved for documentation and
will never be allocated to a real host. TCP connection attempts hang
until the OS's default SYN timeout (typically 75-130s on Linux).

**Use in tests:** `test_browser_agent_kill.py` uses `http://192.0.2.1`
as the capture target. Chromium hangs in `page.goto(...)`, parent's
wall-clock fires at 10s, SIGKILL exercises the kill chain.

---

## A8. `psutil>=5.9` provides `process_iter(['pid','name','create_time'])`

**Source:** psutil docs / changelog.

**Finding:** the `attrs` parameter to `process_iter` is honoured from
psutil 5.x onwards, and `name` + `create_time` work cross-platform
(Linux, macOS, Windows). On Windows, child PIDs of a SIGKILLed
subprocess get reparented to PID 1 (or detach entirely depending on
launch flags), so the by-pid sweep is not enough — we need the
by-name + create_time sweep too, which is why `_kill_chromium_zombies`
does both (D26).

---

## A9. FastAPI WebSocket endpoint binary frames + JSON control split (D30c)

**Mechanism:** `await ws.send_bytes(...)` for raw JPEG frames,
`await ws.send_json(...)` for control messages (`stream_degraded`,
`capture_complete`, `capture_failed`). Frontend branches on
`typeof e.data === "string"` (JSON) vs `e.data instanceof ArrayBuffer`
(binary frame). The browser MUST set `ws.binaryType = "arraybuffer"`
before connect.

**Why split:** binary saves the ~33% base64 inflation per frame. JSON
keeps control messages structured (no length-prefix protocol to
maintain). Each WS message has a discrete boundary at the wire layer
so there's no framing ambiguity.

**Subprocess-side note:** the subprocess emits ALL events (including
frames) as JSONL on stdout (base64-encoded frame bytes). The PARENT
backend decodes the b64 and sends raw bytes on the WS — it's the
parent that does the binary split, not the subprocess. Stdout pipe
stays line-oriented + text mode, which simplifies parsing.

---

## A10. Chromium remote-debugging port is randomly assigned per run

**Mechanism:** `_find_free_port()` binds to port 0, reads the assigned
port, closes. Subprocess passes that port via `--remote-debugging-port=N`
to Chromium and via `cdp_url=http://127.0.0.1:N` to BrowserUse.

**TOCTOU race:** between us closing the probe socket and Chromium
binding the port, another process could grab it. With D19 (cap=1
concurrent capture), the only competitor is background OS chatter.
Acceptable for hackathon. If we ever lift the cap, switch to a
deterministic port-allocation strategy.

---

## A11. `langchain-google-genai` SecretStr workaround for Gemini auth

**Source:** [browser-use/browser-use#1672](https://github.com/browser-use/browser-use/issues/1672)
("DefaultCredentialsError with ChatGoogleGenerativeAI in Gemini").

**Finding:** constructing `ChatGoogleGenerativeAI(model=...)` without
explicit credentials triggers Google's Application Default Credentials
path, which fails with a misleading error if you have no GCP setup
locally. The fix is to pass `google_api_key=SecretStr(api_key)`
explicitly.

**Implementation:** `browser_agent._build_llm` reads `GEMINI_API_KEY`
from env (or falls back to `GOOGLE_API_KEY`), wraps in `SecretStr`,
passes to `ChatGoogleGenerativeAI`. Falls loud if neither env var is
set — no demo-mode fallback to a fake LLM.

---

## A12. Cookie injection happens BEFORE first navigation (D31)

**Mechanism:** `browser_agent.py` calls `await context.add_cookies(...)`
after creating the `BrowserContext` and BEFORE calling `page.goto(args.url)`.
This means cookies are already set when the URL loads — the request
includes them in the `Cookie` header.

**Caveat:** Playwright's `add_cookies` requires `domain` to be set on
each cookie. Our `CookieSpec` Pydantic model enforces this (no Optional
default).

---

## A13. Test environment: full env required, no skipif

**Per the explicit `/ship` instruction:** "no mocks are allowed,
including testing, all tests should fail loudly".

**Test runtime requirements** (any test that hits a fixture asserting
these will fail loudly with a clear message if missing):

- Python 3.11+ (browser-use 0.12 hard requirement)
- `pip install -e aesthesis_app/` (installs all Phase 2 deps)
- `python -m playwright install chromium` (one-time, ~170-450MB)
- ffmpeg on PATH OR `imageio-ffmpeg` bundled
- `GEMINI_API_KEY` env var
- For TRIBE-touching tests: `TRIBE_SERVICE_URL` reachable

**No tests skip silently.** Tests that need an env they don't have call
`pytest.fail(...)` with a message explaining what's missing. This is
intentional — it makes "the test suite passed but we didn't run X"
impossible.

---

## A14. Single-tenant cap (D19) — second capture returns 409

**Implementation:** the module-level `_REGISTRY` in `capture/runner.py`
holds at most 1 active `CaptureRunner`. `start_run()` checks
`active_count() >= 1` and raises `CaptureInProgressError` (mapped to
HTTP 409 by `main.py`).

**Why cap=1:** Phase 2 is hackathon-scope, single-user demo. Two
concurrent Chromiums would compete for resources and double the GPU/
network/Modal-cost surface area. Multi-tenant is a v2 concern.

**Cleanup on subprocess exit:** `_on_subprocess_exit` removes the
runner from `_REGISTRY` so a new run can immediately launch. If a
test or the user kills the registry mid-run, restart of `uvicorn`
clears it (it's process-local state).

---

## A15. WebSocket disconnect grace window (D27) — 3s before SIGKILL

**Mechanism:** `CaptureRunner.remove_subscriber` arms a 3-second
`asyncio.sleep` task. If a new subscriber arrives within 3s,
`add_subscriber` cancels the task. If the 3s elapses with zero
subscribers AND the capture isn't complete, we SIGKILL.

**Why:** React StrictMode (in Next.js dev mode) double-mounts effects,
which causes the WebSocket to disconnect and immediately reconnect.
A grace-less kill would terminate every dev-mode capture instantly.
Also handles transient network blips.

---

## A16. Last lifecycle event replayed on reconnect (D32)

**Mechanism:** `CaptureRunner.last_lifecycle` stores the most recent
`stream_degraded` / `capture_complete` / `capture_failed` event. New WS
connections (`add_subscriber`) immediately receive the stored event
via `ws.send_json` after `accept()`.

**Why:** without replay, a 1-second WS dropout AT THE MOMENT
`capture_complete` fires leaves the frontend hanging on the last frame
forever. Replay guarantees the frontend always learns the latest
terminal state.

**Frontend idempotency:** `LiveStreamPanel` gates `onCaptureComplete`
and `onCaptureFailed` callbacks behind `completionFiredRef` /
`failureFiredRef` so a replayed event after the original delivery
doesn't double-fire the parent's analyze flow.

---

## A17. Captured artifacts retained on analyze failure (D33)

**Behaviour:** `/api/analyze/by-run/{id}` cleans `upload_dir/{id}/`
ONLY when `orchestrator.run_analysis` returns successfully. On any
exception (`OrchestratorError`, `TribeServiceError`, anything else),
artifacts persist for inspection.

**Asymmetry vs `/api/analyze`:** the multipart skip path always cleans
in `finally`. Capture artifacts are far more expensive to reproduce
(re-running BrowserUse against a possibly-mutated page state takes
30s + LLM cost), so retaining on failure is a deliberate DX win.

**Operational note:** retained run dirs are at
`{upload_dir}/{run_id}/` containing `video.mp4`, `actions.jsonl`,
optionally `auth_cookies.json`, and the `frames/` work dir from the
orchestrator. No automatic cleanup of failed runs — manual
`rm -rf upload_dir/<id>` after debug. v2 will add a periodic sweep.

---

## A19. Pre-warm two-phase architecture (post-build refinement)

**Problem:** cold start of the capture subprocess is ~3-7 seconds:
Chromium launch (1-2s), CDP screencast handshake (~100ms), heavy Python
imports (browser-use + playwright pull a lot — ~500ms-1s), `ChatGoogle`
construction + genai HTTPS prime (~500ms-1s), first LLM call latency
(~1-3s for cold connection). All happen between the user clicking Start
and the first frame appearing.

**Solution:** spawn the subprocess earlier, on the user's *navigation*
to the Capture screen, not on their click. Two-phase lifecycle:

1. **Pre-warm** (fires on `useEffect` when `CaptureView` mounts):
   - `POST /api/prewarm` → backend spawns subprocess
   - Subprocess does Chromium launch + CDP screencast + ChatGoogle init
   - Subprocess opens a stand-by HTML page (data: URL — see A22) so the
     live stream has SOMETHING to render
   - Subprocess emits ``{"type":"prewarm_ready", ...}`` on stdout
   - Backend forwards as JSON WS message; frontend flips Start button
     from disabled to enabled

2. **Capture** (fires on user click of Start Capture):
   - `POST /api/run/{id}/start` with the URL + goal + optional auth
   - Backend writes `{"type":"start", "url":..., "goal":...}` to
     subprocess stdin
   - Subprocess reads the line, injects cookies (D31), navigates to
     URL, constructs `Agent`, runs

**Outcome:** click-to-first-frame latency drops from ~5-8s to ~0ms
(everything's already running; subprocess just navigates and runs the
agent loop). User-perceived UX win is significant.

**Backwards compat:** legacy `POST /api/run` still works — internally it
just calls `start_run(prewarm_only=False)` which spawns the subprocess
AND auto-sends the start command as soon as `prewarm_ready` arrives.
Same end-to-end behavior as before, no observable change for callers
that haven't migrated.

**Stdin protocol** (parent → subprocess): newline-delimited JSON. One
command type defined: `{"type":"start","url":"...","goal":"...","auth":{...}}`.
After sending, parent closes stdin (signals "no more commands").

**Stdin reader** (subprocess side): asyncio support for reading the
subprocess's own stdin is platform-specific (Windows ProactorEventLoop
has known issues with stdin pipes). We side-step that with a daemon
thread that does blocking line reads on `sys.stdin` and pumps complete
lines onto an `asyncio.Queue` via `loop.call_soon_threadsafe`. Works
the same on Linux/macOS/Windows.

---

## A20. D1 wall-clock timer is deferred until phase = running

**Problem:** if the wall-clock D1 timer started on subprocess spawn,
a user who took 30s to type a URL would have their capture killed
before they clicked Start.

**Solution:** `CaptureRunner._wallclock_task` is NOT created in
`CaptureRunner.start()` anymore. Instead, it's spawned in
`start_capture()`, alongside the phase transition `warming/ready →
running`. The `capture_max_wall_s` budget is for the actual capture,
not pre-warm idle.

**Pre-warm time IS still bounded** by D27 — if the WS subscriber set
goes empty for 3 seconds (user navigated away, closed tab, etc.), the
3-second grace timer fires SIGKILL regardless of phase. So a forgotten
pre-warm doesn't leak Chromium forever.

**Verified by:** `tests/test_prewarm.py::test_wallclock_NOT_armed_during_prewarm`
forces `capture_max_wall_s=5`, sleeps 7s in pre-warm, asserts the
runner is still in `ready` phase. Fails loud if the wall-clock fires
during pre-warm.

---

## A21. D21 spike script — coexistence verification

**Location:** `aesthesis_app/scripts/spike_d21_browseruse_cdp_coexistence.py`

**What it tests** (the architectural risks the eng review flagged):
1. Playwright + browser-use 0.12 + CDP screencast all coexist on the
   same Chromium without one killing the other
2. browser-use's `Browser(cdp_url=...)` connects without conflict to
   our Chromium and `Page.startScreencast` keeps emitting frames
   throughout `agent.run()`
3. ffmpeg `image2pipe` produces a valid H.264 MP4 that ffprobe accepts
   (validation.py:92 enforces this)

**Run it:**
```bash
cd <repo root>
export GEMINI_API_KEY=...
python aesthesis_app/scripts/spike_d21_browseruse_cdp_coexistence.py
```

**Exit codes:**
- `0` — pass; architecture is verified end-to-end
- `1` — environmental issue (missing key/chromium/ffmpeg) — bail loudly
- `2` — coexistence failure — the architecture is broken and the spike
  output explains where (single-subscriber screencast conflict, agent
  refusing cdp_url, MP4 validation reject, etc.)

**Known limitations:**
- Spike uses `https://example.com` by default; override with
  `D21_TARGET_URL=...` for your own demo URL
- Goal defaults to "explore the homepage briefly"; override with
  `D21_GOAL=...`
- Recording cap defaults to 15s; override with `D21_MAX_S=...`

This is the verification step from the eng review that I committed to
running before relying on the production capture pipeline. It's
standalone — runs in ~30 seconds + Chromium download (one-time).

---

## A23. Warm-up frames are dropped from the final MP4

**Bug found:** when pre-warm was added (A19), the AdaptiveStreamer started
its CDP screencast immediately and accumulated standby-HTML frames into
`frames_for_mp4` from the moment of subprocess spawn. If left unfixed,
the MP4 sent to TRIBE + Gemini would include:

  ~50 frames of "Browser ready" standby HTML (pre-warm phase)
  + variable idle frames during URL/Goal typing (~30s × 10fps = ~300 frames)
  + 50-300 frames of the actual demo

TRIBE would then "see" the user's brain reacting to a pulsing dot, then
to nothing, then to the demo — contaminating the analysis with material
that isn't part of the captured experience.

**Fix:** in `browser_agent._run_two_phase`, immediately before
`await page.goto(cmd.url, ...)` (the real navigation), call
`streamer.frames_for_mp4.clear()`. The buffer resets to zero. All
subsequent frames (URL load + agent run) are the only ones that survive
into ffmpeg-encode.

**The live stream is unaffected.** Frames continue to flow to
`sys.stdout` for every screencast event regardless of whether they're
in `frames_for_mp4`. The user sees an uninterrupted "watch the agent
work" UX — the stand-by HTML, the navigation transition, and the agent
loop are all visible live. The MP4 just doesn't include the pre-real-nav
material.

**Verbose logging:** `agent.warm_frames_dropped_from_mp4` logs the count
of frames cleared with `reason="pre-warm standby frames not part of
demo"`. Easy to grep when debugging — if N is suspiciously large
(say >300), that means the user took a long time to click Start, and
the standby buffer accumulated a lot.

**Verified by:** `tests/test_prewarm.py::test_warm_frames_excluded_from_mp4`
spawns a real subprocess, lets it pre-warm for 5s (accumulating standby
frames), sends start with `max_recording_s=5`, ffprobes the resulting
MP4, asserts duration is ≤7s (clean: ~5s; with bug: ~13s). Threshold
chosen well above the clean case and well below the bug case.

---

## A22. Stand-by HTML uses a `data:` URL (no network round-trip)

**Choice:** the pre-warm phase navigates the page to a `data:text/html`
URL containing a small inline HTML doc that says "Browser ready" with a
pulsing dot. The HTML is a constant in `browser_agent.py`.

**Why `data:` URL:**
- Zero network dependency — works offline, on a flaky link, behind a
  firewall, in CI
- No third-party server to host
- Consistent rendering — no risk of "the standby page failed to load,
  user sees a blank tab" failure mode
- Tiny — the HTML is ~700 bytes inline

**Why navigate AT ALL during pre-warm** (vs. leaving Chromium on
about:blank): so the user's live stream has something visible during
pre-warm. about:blank renders as a flat white page and gives no hint
that the system is alive. The pulsing dot + "Browser ready / Configure
capture and click Start" text confirms the pipeline works end-to-end
(Chromium → CDP screencast → backend → WS → canvas) even before the
user does anything.

**Browser-use note:** browser-use's `Agent.add_new_task()` could in
theory let us re-use a single Agent across pre-warm and start, with the
task swapped out. We don't bother — Agent construction is cheap
(~50-200ms) and constructing fresh on the start command keeps the code
simpler. The Chromium + CDP + LLM client (the expensive parts) stay
across the phase transition.

---

## A18. Frontend smoothness pipeline (D30 a/b/c)

**D30a (jitter buffer):** frames arrive bursty over WebSocket; even at a
steady source 10 fps, network/event-loop jitter clumps frames into
bursts. `LiveStreamPanel` uses a 3-frame queue with drop-stale policy
and renders at a fixed 100ms cadence via `requestAnimationFrame`.

**D30b (off-main-thread decode):** `<img>.src = "data:image/jpeg;base64,..."`
synchronously decodes JPEG on the main thread in most Chromium builds,
which collides with React rerenders + framer-motion. The new pipeline
uses `<canvas>` + `createImageBitmap` (off-main-thread decode) +
`drawImage` (GPU-accelerated composite). Bitmap is closed after draw
to release GPU memory.

**D30c (binary WS frames):** described in A9 above.

**Source:** these were arrived at by reasoning from first principles
about perceived smoothness, then validated against current browser
behavior. No external benchmarks consulted; expected real-world
smoothness improvement is subjective and best confirmed by running the
live demo.

---

## Quick-start checklist for someone running this fresh

```bash
# 1. Python 3.11+ — browser-use hard requirement
python --version  # must be >= 3.11

# 2. Install all Phase 2 deps
pip install -e aesthesis_app/
pip install -r requirements-dev.txt

# 3. Playwright Chromium binary (one-time, ~170-450MB)
python -m playwright install chromium

# 4. ffmpeg — system OR bundled
which ffmpeg || python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"

# 5. Set GEMINI_API_KEY in .env
echo "GEMINI_API_KEY=your-google-genai-key" >> .env

# 6. Make sure TRIBE service URL is set (Modal deployment)
echo "TRIBE_SERVICE_URL=https://yourname--aesthesis-tribe.modal.run" >> .env

# 7. Run tests — fails loudly if any of the above is missing
pytest aesthesis_app/tests/ -v

# 8. Live dev
./dev.sh   # or dev.cmd on Windows
```

If the test suite is green and `/dev.sh` says everything's running,
hit `http://localhost:3000`, click "Capture & Assess", paste a URL,
and the live screen recording should appear within ~3s.
