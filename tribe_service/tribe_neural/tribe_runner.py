"""Thin wrapper around `tribev2.demo_utils.TribeModel.predict`.

Two modes:
- **real**: imports `tribev2`, loads `facebook/tribev2`, runs actual inference.
  Requires GPU + heavy deps (torch, V-JEPA 2, DINOv2, Wav2Vec-BERT, LLaMA 3.2).
- **mock**: returns deterministic synthetic predictions whose shape and
  approximate magnitude resemble the real model. Used by tests, by the dev
  loop on a laptop without a GPU, and by the CI harness.

The real path follows the public TRIBE v2 demo notebook
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
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from .constants import EDGE_TR_TRIM, NUM_VERTICES, TR_DURATION
from .validation import PipelineError

log = logging.getLogger(__name__)


class TribeRunner(ABC):
    @abstractmethod
    def predict_video(self, video_path: str | Path) -> np.ndarray:
        """Run TRIBE on the given MP4. Returns (n_TRs, NUM_VERTICES)."""


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


class RealTribeRunner(TribeRunner):
    """Real TRIBE v2 inference. Heavy deps loaded lazily on first call so
    `import tribe_runner` itself never pulls torch / tribev2 into memory."""

    def __init__(self) -> None:
        self._model = None  # type: ignore[assignment]

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        log.info("loading TRIBE v2 model (first call) — may take several minutes")
        try:
            # Imports are lazy: we only want torch + tribev2 in process when
            # the GPU worker actually needs them.
            from tribev2.demo_utils import TribeModel  # type: ignore
        except ImportError as e:  # pragma: no cover — exercised on GPU worker
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


class MockTribeRunner(TribeRunner):
    """Synthesizes (n_TRs, NUM_VERTICES) predictions that look enough like
    real TRIBE output to exercise the rest of the pipeline.

    `n_TRs` is derived from the video duration if ffmpeg is importable,
    otherwise defaults to 30 TRs (~45 seconds — matches the canonical
    30s-clip demo per DESIGN.md D7 with a small TRIBE warmup).
    """

    def __init__(self, default_trs: int = 30, seed: int = 7) -> None:
        self.default_trs = default_trs
        self._rng = np.random.default_rng(seed)
        log.info("mock TRIBE runner active (default n_TRs=%d)", default_trs)

    def _probe_n_trs(self, video_path: str | Path) -> int:
        try:
            import ffmpeg  # type: ignore
        except ImportError:
            return self.default_trs
        try:
            probe = ffmpeg.probe(str(video_path))
            duration = float(probe["format"]["duration"])
            return max(4, int(round(duration / TR_DURATION)))
        except Exception as e:  # noqa: BLE001
            log.debug("mock probe failed (%s); using default n_TRs=%d",
                      e, self.default_trs)
            return self.default_trs

    def predict_video(self, video_path: str | Path) -> np.ndarray:
        n_trs = self._probe_n_trs(video_path)
        log.info(
            "mock TRIBE inference",
            extra={"step": "tribe", "video": str(video_path),
                   "n_trs": n_trs, "mock": True},
        )
        # Smooth random-walk per-vertex (low-frequency drift + small noise).
        # This produces output where:
        #   - mean is ~0
        #   - per-vertex std varies
        #   - individual vertices show plausible up/down trajectories
        # All of which makes ROI extract + composite math non-trivial.
        base = self._rng.standard_normal((n_trs, NUM_VERTICES)) * 0.3
        # Add a low-frequency component so spike detection has interesting input
        t_axis = np.linspace(0.0, 6.28, n_trs)
        for k in range(8):
            phase = self._rng.uniform(0.0, 6.28)
            amp = self._rng.uniform(0.1, 0.4)
            wave = amp * np.sin(t_axis * (k + 1) + phase)
            mask = self._rng.random(NUM_VERTICES) > 0.7
            base[:, mask] += wave[:, None]
        # Add a couple of synthetic "events" (sharp spikes in random vertex
        # subsets) so spike detection in downstream code has something to find.
        for _ in range(min(3, max(1, n_trs // 10))):
            t_event = int(self._rng.integers(2, max(3, n_trs - 2)))
            mask = self._rng.random(NUM_VERTICES) > 0.92
            base[t_event, mask] += self._rng.uniform(1.5, 3.0)
        return _trim_edge_trs(base.astype(np.float64))


def build_runner(mock: bool) -> TribeRunner:
    return MockTribeRunner() if mock else RealTribeRunner()
