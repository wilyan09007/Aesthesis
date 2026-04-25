"""Runtime configuration.

All values are environment-driven — no config file. The Pydantic settings
object validates types at startup so a bad env var fails loudly.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Loaded once on app startup. Read-only after that."""

    tribe_service_url: str = Field(
        default_factory=lambda: os.getenv(
            "TRIBE_SERVICE_URL", "http://localhost:8001"
        ),
        description="Base URL of the TRIBE service. The orchestrator POSTs "
                    "/process_video_timeline against this.",
    )
    tribe_request_timeout_s: float = Field(
        default_factory=lambda: float(os.getenv("TRIBE_REQUEST_TIMEOUT_S", "300"))
    )
    upload_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("UPLOAD_DIR", "./uploads")).resolve()
    )
    max_upload_bytes: int = Field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    )
    max_duration_s: float = Field(
        default_factory=lambda: float(os.getenv("MAX_DURATION_S", "30"))
    )
    max_width: int = Field(
        default_factory=lambda: int(os.getenv("MAX_WIDTH", "1920"))
    )
    max_height: int = Field(
        default_factory=lambda: int(os.getenv("MAX_HEIGHT", "1080"))
    )

    gemini_api_key: str = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "")
    )
    gemini_model_insights: str = Field(
        default_factory=lambda: os.getenv("GEMINI_MODEL_INSIGHTS", "gemini-2.0-flash")
    )
    gemini_model_verdict: str = Field(
        default_factory=lambda: os.getenv("GEMINI_MODEL_VERDICT", "gemini-2.0-flash")
    )

    cleanup_uploads: bool = Field(
        default_factory=lambda: os.getenv("CLEANUP_UPLOADS", "1").lower()
        in ("1", "true", "yes"),
        description="Delete uploaded MP4s after results are returned.",
    )

    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            o.strip() for o in os.getenv(
                "CORS_ALLOW_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",") if o.strip()
        ],
        description=(
            "Origins allowed to call /api/analyze from the browser. "
            "Set CORS_ALLOW_ORIGINS=* to allow any (dev only). "
            "Defaults cover the Next.js dev server."
        ),
    )

    # ── Phase 2 capture (DESIGN.md §§4.1, 4.2, 4.2b) ─────────────────────
    chromium_headless: bool = Field(
        default_factory=lambda: os.getenv("CHROMIUM_HEADLESS", "1").lower()
        in ("1", "true", "yes"),
        description=(
            "Whether the BrowserUse subprocess launches Chromium headless. "
            "Default true. Flip to false (CHROMIUM_HEADLESS=0) for visual "
            "debugging — note that headed mode requires a desktop session."
        ),
    )
    browseruse_model: str = Field(
        default_factory=lambda: os.getenv("BROWSERUSE_MODEL", "gemini-2.5-pro"),
        description=(
            "Gemini model name BrowserUse uses to pick actions. Reuses "
            "GEMINI_API_KEY. Pro > Flash for action accuracy on complex pages."
        ),
    )
    capture_max_wall_s: float = Field(
        default_factory=lambda: float(os.getenv("CAPTURE_MAX_WALL_S", "90")),
        description=(
            "D1 hard wall-clock cap for the capture subprocess. Parent "
            "SIGKILLs after this many seconds — protects against BrowserUse "
            "hangs that ignore internal timeouts (issues #1157, #3615)."
        ),
    )
    capture_recording_cap_s: float = Field(
        default_factory=lambda: float(os.getenv("CAPTURE_RECORDING_CAP_S", "30")),
        description=(
            "D7 maximum CDP screencast duration. Subprocess stops the "
            "screencast and finalizes the MP4 after this many seconds "
            "even if BrowserUse hasn't returned. Inner cap — wall-clock "
            "(capture_max_wall_s) is the outer safety net."
        ),
    )
    capture_viewport_width: int = Field(
        default_factory=lambda: int(os.getenv("CAPTURE_VIEWPORT_WIDTH", "1280")),
        description="Chromium viewport width for the captured tab.",
    )
    capture_viewport_height: int = Field(
        default_factory=lambda: int(os.getenv("CAPTURE_VIEWPORT_HEIGHT", "720")),
        description="Chromium viewport height for the captured tab.",
    )
    cached_demos_dir: Path = Field(
        default_factory=lambda: Path(
            os.getenv("CACHED_DEMOS_DIR", "./aesthesis_app/cached_demos")
        ).resolve(),
        description=(
            "D29 stage-day fallback. GET /api/cached-demos enumerates "
            "MP4s in this dir (reads MANIFEST.json for url+label mapping). "
            "Frontend offers a one-click 'Use cached demo' button on "
            "capture_failed when the requested URL matches a cached entry."
        ),
    )

    def model_post_init(self, _ctx) -> None:  # noqa: D401
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        # cached_demos_dir is optional — only mkdir if explicitly set.
        # (Default path may not exist on a fresh clone.)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
