"""Capture pipeline — URL + Goal -> BrowserUse-driven Chromium ->
CDP screencast (live frames) + ffmpeg-stitched H.264 MP4 -> existing
TRIBE analysis pipeline.

DESIGN.md §§4.1, 4.2, 4.2b (post-§17 single-URL scoping) plus the
post-OV refinements D11-D33 captured in
``~/.claude/plans/dazzling-tinkering-sedgewick.md``.

Two halves:

- **Parent side (this process)** — ``runner.CaptureRunner`` owns the
  subprocess lifecycle, the kill chain (D1 + D26 by-name + by-pid sweep
  on Windows and Linux), the WebSocket subscriber set with 3s grace
  window (D27), and last-lifecycle-event replay on reconnect (D32).
  Concurrent captures capped at 1 per backend instance (D19).

- **Subprocess side (``browser_agent.py`` at the top of the
  ``aesthesis`` package, run as ``python -m aesthesis.browser_agent``)**
  Launches Chromium via Playwright with ``--remote-debugging-port=PORT``
  + a ``new_cdp_session`` for ``Page.startScreencast``. Hands the same
  Chromium to BrowserUse via ``BrowserSession(cdp_url=...)`` so the
  agent drives the same tab the streamer is sampling. Frames flow:
  CDP → ``streamer.AdaptiveStreamer.on_frame`` → stdout JSONL (live
  fan-out, parent forwards as binary WS frames per D30c) AND
  in-memory list (encoded to H.264 MP4 at end via ffmpeg). One frame
  source, two consumers.

Why CDP screencast and not Playwright ``record_video_dir``: validation
hard-requires H.264 (``validation.py:92``) but Playwright recordVideo
emits WebM. Going CDP-only avoids the WebM→H.264 transcode AND the
known coexistence sharp edge between recordVideo on a Playwright
context and a CDP-connected BrowserUse session
(see ``ASSUMPTIONS.md`` for the full research log).
"""
