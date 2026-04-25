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

    def model_post_init(self, _ctx) -> None:  # noqa: D401
        self.upload_dir.mkdir(parents=True, exist_ok=True)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
