"""Pipeline error types.

Kept distinct so the API layer can decide on HTTP status codes:
- ValidationError -> 400
- PipelineError   -> 500
"""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Something broke inside the inference pipeline (TRIBE call, ROI extract,
    etc). Treat as 500 — caller can retry."""


class ValidationError(ValueError):
    """The request payload itself was invalid (bad path, unsupported option).
    Treat as 400 — caller must fix and retry."""
