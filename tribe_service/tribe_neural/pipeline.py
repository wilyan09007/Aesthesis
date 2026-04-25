"""End-to-end pipeline for the `/process_video_timeline` endpoint.

    MP4 path
       │
       ▼
    runner.predict_video()         ← step1b (TRIBE v2 inference)
       │   (n_TRs, 20484)
       ▼
    extract_all()                  ← step2 (per-vertex preds -> 8 ROI series)
       │   dict[name -> ndarray(n_TRs,)]
       ▼
    build_timeline()               ← step7 (per-TR frames + sliding windows)
       │   dict (frames, windows, roi_series, ...)
       ▼
    response payload (+ processing_time_ms attached by caller)

Every step is timed and logged. Errors are wrapped in PipelineError so the
API layer can pick a sensible HTTP status.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .constants import STEP_TRS_DEFAULT, WINDOW_TRS_DEFAULT
from .init_resources import Resources
from .logging_config import timed_step
from .steps.step2_roi import extract_all
from .steps.step2b_parcels import extract_parcels
from .steps.step7_timeline import build_timeline
from .validation import PipelineError, ValidationError

log = logging.getLogger(__name__)


def process_video_timeline(
    video_path: str | Path,
    resources: Resources,
    *,
    window_trs: int = WINDOW_TRS_DEFAULT,
    step_trs: int = STEP_TRS_DEFAULT,
    run_id: str | None = None,
) -> dict:
    """Run the full inference pipeline against a single MP4.

    Args:
        video_path: local filesystem path. Caller is responsible for
            transferring the file from the app server to the GPU worker.
        resources: pre-loaded `Resources` instance from `load_resources`.
        window_trs / step_trs: sliding-window config (DESIGN.md §5.10).
        run_id: optional opaque ID; if set, every log line emitted by this
            function tags itself with the ID for cross-service tracing.

    Returns:
        Dict with `frames`, `windows`, `roi_series`, `n_trs`, `tr_duration_s`,
        `window_config`, plus `processing_time_ms`.

    Raises:
        ValidationError: if the inputs are invalid (missing file, bad shape).
        PipelineError: if TRIBE inference itself fails.
    """
    video_path = Path(video_path).expanduser()
    if not video_path.exists():
        raise ValidationError(f"video_path does not exist: {video_path}")
    if window_trs < 2 or step_trs < 1:
        raise ValidationError(
            f"invalid window config: window_trs={window_trs}, step_trs={step_trs}"
        )

    log_extra = {"run_id": run_id} if run_id else {}
    log.info(
        "process_video_timeline begin",
        extra={**log_extra, "step": "pipeline", "video": str(video_path),
               "window_trs": window_trs, "step_trs": step_trs},
    )

    pipeline_t0 = time.perf_counter()
    try:
        with timed_step(log, "tribe", **log_extra) as ctx:
            preds = resources.runner.predict_video(video_path)
            ctx["shape"] = list(preds.shape)
            ctx["n_trs"] = int(preds.shape[0])

        with timed_step(log, "roi", **log_extra) as ctx:
            roi_ts = extract_all(preds, resources.masks, resources.weight_maps)
            ctx["n_rois"] = len(roi_ts)

        # ── Step 2b: per-parcel reduction (for cortical brain rendering) ──
        # Optional: only runs if the Schaefer parcel map is loaded. The
        # 8-ROI chart and Gemini synthesizer don't depend on this. When
        # the map is missing, the frontend gracefully falls back to the
        # placeholder brain. See ASSUMPTIONS_BRAIN.md §1.3 + §3.6.
        parcel_series = None
        if resources.parcels is not None:
            with timed_step(log, "parcels", **log_extra) as ctx:
                arr = extract_parcels(preds, resources.parcels)
                # Cast to nested Python lists for JSON-friendly transport.
                # Wire size: ~32 KB for a 30s clip — see ASSUMPTIONS_BRAIN.md §1.3.
                parcel_series = arr.tolist()
                ctx["n_trs"] = int(arr.shape[0])
                ctx["n_parcels"] = int(arr.shape[1])
        else:
            log.info(
                "skipping parcel extraction (no parcel map loaded) — "
                "cortical brain will render placeholder",
                extra={"step": "parcels", **log_extra},
            )

        with timed_step(log, "timeline", **log_extra) as ctx:
            payload = build_timeline(
                roi_ts,
                window_trs=window_trs,
                step_trs=step_trs,
            )
            ctx["n_frames"] = len(payload["frames"])
            ctx["n_windows"] = len(payload["windows"])

        # Attach parcel_series to the payload alongside roi_series. None
        # is allowed — schemas.py marks the field Optional.
        payload["parcel_series"] = parcel_series
    except (ValidationError, PipelineError):
        raise
    except Exception as e:  # noqa: BLE001
        raise PipelineError(f"pipeline failed: {e}") from e

    processing_time_ms = round((time.perf_counter() - pipeline_t0) * 1000.0, 2)
    payload["processing_time_ms"] = processing_time_ms

    log.info(
        "process_video_timeline done",
        extra={**log_extra, "step": "pipeline",
               "elapsed_ms": processing_time_ms,
               "n_frames": len(payload["frames"]),
               "n_windows": len(payload["windows"])},
    )
    return payload
