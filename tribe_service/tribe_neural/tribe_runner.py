"""Thin wrapper around `tribev2.demo_utils.TribeModel.predict`.

Imports `tribev2`, loads `facebook/tribev2`, runs actual inference.
Requires GPU + heavy deps (torch, V-JEPA 2, DINOv2, Wav2Vec-BERT, LLaMA 3.2).

The call shape follows the public TRIBE v2 demo notebook
(https://github.com/facebookresearch/tribev2/blob/main/tribe_demo.ipynb):

    model = TribeModel.from_pretrained("facebook/tribev2", cache_folder=...)
    df = model.get_events_dataframe(video_path=video_path)
    preds, segments = model.predict(events=df)
    # preds.shape == (n_timesteps, n_vertices) where n_vertices = 20484

Edge-TR trim (drop last 2 TRs to remove a Transformer boundary artifact —
DESIGN.md §5.6 #4 / §5.15.5) is applied here so callers always see clean
outputs. The trim is empirically up for re-validation under the video
pathway; default is to apply it, set `EDGE_TR_TRIM_DISABLE=1` to skip.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

from .constants import EDGE_TR_TRIM, NUM_VERTICES
from .validation import PipelineError

log = logging.getLogger(__name__)


def _trim_edge_trs(preds: np.ndarray) -> np.ndarray:
    """Apply DESIGN.md §5.6 #4 edge-TR trim — drop the last 2 TRs.

    Skipped if (a) the array is too short to survive trimming, or (b) the
    user set `EDGE_TR_TRIM_DISABLE=1`."""
    if os.getenv("EDGE_TR_TRIM_DISABLE", "0").lower() in ("1", "true", "yes"):
        return preds
    if preds.shape[0] <= EDGE_TR_TRIM + 2:
        log.debug("edge-TR trim skipped — n_TRs=%d too small", preds.shape[0])
        return preds
    return preds[:-EDGE_TR_TRIM]


class TribeRunner:
    """Real TRIBE v2 inference. Heavy deps loaded lazily on first call so
    `import tribe_runner` itself never pulls torch / tribev2 into memory."""

    def __init__(self) -> None:
        self._model = None  # type: ignore[assignment]

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        log.info("loading TRIBE v2 model (first call) — may take several minutes")
        try:
            from tribev2.demo_utils import TribeModel  # type: ignore
        except ImportError as e:
            raise PipelineError(
                "tribev2 package not installed. "
                "Run `pip install git+https://github.com/facebookresearch/tribev2`."
            ) from e
        cache_folder = Path(os.getenv("TRIBE_DATA_DIR", "./data")) / "cache"
        cache_folder.mkdir(parents=True, exist_ok=True)
        self._model = TribeModel.from_pretrained(
            "facebook/tribev2",
            cache_folder=str(cache_folder),
        )
        log.info("TRIBE v2 model ready (cache=%s)", cache_folder)

    def predict_video(self, video_path: str | Path) -> np.ndarray:
        self._ensure_model()
        assert self._model is not None
        video_path = str(Path(video_path).expanduser().resolve())
        log.info("TRIBE inference start", extra={"step": "tribe", "video": video_path})

        t0 = time.perf_counter()
        df = self._model.get_events_dataframe(video_path=video_path)
        events_ms = (time.perf_counter() - t0) * 1000.0
        log.debug("TRIBE events extracted in %.1fms (rows=%d)", events_ms, len(df))

        t1 = time.perf_counter()
        preds, _segments = self._model.predict(events=df)
        infer_ms = (time.perf_counter() - t1) * 1000.0
        if hasattr(preds, "cpu"):
            preds = preds.cpu().numpy()  # type: ignore[union-attr]
        preds = np.asarray(preds, dtype=np.float64)

        if preds.ndim != 2 or preds.shape[1] != NUM_VERTICES:
            raise PipelineError(
                f"TRIBE returned unexpected shape {preds.shape}; "
                f"expected (n_TRs, {NUM_VERTICES})"
            )
        if not np.isfinite(preds).all():
            raise PipelineError("TRIBE prediction contains NaN/Inf")
        log.info(
            "TRIBE inference done",
            extra={"step": "tribe", "shape": list(preds.shape),
                   "events_ms": round(events_ms, 1),
                   "infer_ms": round(infer_ms, 1)},
        )
        return _trim_edge_trs(preds)
