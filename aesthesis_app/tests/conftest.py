"""Shared pytest fixtures and environment validation.

NO MOCKS. Per project memory ``feedback_no_mocks`` and the explicit user
instruction with /ship: tests fail loudly when an environmental
requirement is missing — they do NOT skip silently.

What's required to run the full Phase 2 test suite:

- Python 3.11+ (browser-use 0.12 hard-requires)
- ``pip install -e aesthesis_app/`` to install the project
- ``python -m playwright install chromium`` (one-time, ~170-450MB)
- ffmpeg on PATH OR ``imageio-ffmpeg`` bundled binary
- ``GEMINI_API_KEY`` env var set to a valid Google Generative AI key
- For TRIBE-touching tests: ``TRIBE_SERVICE_URL`` reachable AND a Modal
  GPU worker either warm or willing to cold-start (~30-60s)

If any of the above is missing, tests that depend on it will fail with
a clear error explaining what's missing. They do not skip.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


def _require(condition: bool, message: str) -> None:
    """Fail the test loudly if a precondition isn't met. Used by env-asserting
    fixtures. We deliberately don't ``pytest.skip`` because the project rule
    is fail-loudly-no-mocks-no-skips."""
    if not condition:
        pytest.fail(message, pytrace=False)


@pytest.fixture(scope="session")
def gemini_api_key() -> str:
    """Require GEMINI_API_KEY (or GOOGLE_API_KEY) at session start."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    _require(
        bool(key),
        "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. The Phase 2 "
        "capture path drives BrowserUse with Gemini and there is no "
        "mock fallback. Set the key in your environment before running "
        "the test suite.",
    )
    return key  # type: ignore[return-value]


@pytest.fixture(scope="session")
def chromium_available() -> None:
    """Require Playwright Chromium to be installed."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        pytest.fail(
            "playwright python package is not installed. Run "
            "`pip install -e aesthesis_app/` to install Phase 2 deps.",
            pytrace=False,
        )

    try:
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
            _require(
                exe and Path(exe).exists(),
                f"playwright reported chromium executable_path={exe!r} but it "
                "doesn't exist on disk. Run `python -m playwright install chromium`.",
            )
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"playwright sync_playwright init failed: {e}. Have you run "
            "`python -m playwright install chromium`?",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def ffmpeg_path() -> str:
    """Require ffmpeg (system PATH or imageio-ffmpeg bundled)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        _require(
            Path(bundled).exists(),
            f"imageio_ffmpeg.get_ffmpeg_exe() returned {bundled!r} but it "
            "doesn't exist on disk.",
        )
        return bundled
    except ImportError:
        pytest.fail(
            "Neither system ffmpeg nor imageio-ffmpeg is available. The "
            "Phase 2 capture pipeline encodes screencast frames to H.264 "
            "MP4 via ffmpeg — install one of:\n"
            "  - System ffmpeg (apt install ffmpeg, brew install ffmpeg, etc.)\n"
            "  - `pip install imageio-ffmpeg` (bundled binary)",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def python_executable() -> str:
    """The Python interpreter path used for spawning subprocesses (so tests
    use the same Python as the test runner — important for venv isolation)."""
    return sys.executable
