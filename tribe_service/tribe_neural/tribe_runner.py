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

    @staticmethod
    def _patch_video_extractor_for_batching() -> None:
        """Replace ``neuralset.extractors.video.HuggingFaceVideo._get_data``
        with a batched version of the V-JEPA forward pass.

        Upstream runs every chunk (27 of them on a 13.7 s clip) through
        V-JEPA-2-vitg as a B=1 forward, with a hard
        ``if t_embd.shape[0] != 1: raise`` check inline. On A100-40GB
        that's ~3.85 s/iter ≈ 104 s of pure GPU encoding, the dominant
        cost in a 148 s end-to-end run.

        This patch collects all chunks first, then runs them through V-JEPA
        in batches of size ``TRIBE_VIDEO_BATCH`` (default 8 — the
        empirical ceiling on A100-40GB with the upstream
        ``predict_hidden_states`` semantics, which concatenate all
        ~40 layers' hidden states into one tensor before per-item
        aggregation). 27 chunks → 4 forwards (8+8+8+3), ~6× the
        per-batch throughput of upstream's B=1 loop. The per-chunk
        aggregations are kept identical to upstream — they're pure
        stateless reductions, so per-item application after a batched
        forward is mathematically equivalent. Expect small numeric
        drift (~1 % on individual ROI values) from batched cuBLAS /
        processor preprocessing; direction and rank order of ROIs are
        preserved.

        Memory budget on A100-40GB (39.49 GB usable), measured FP32.
        The bottleneck isn't activations themselves — it's the
        ``torch.cat`` inside ``_HFVideoModel.predict_hidden_states`` at
        upstream line 478, which briefly holds both the per-layer
        hidden states tuple AND the concatenated ``(B, n_layers,
        n_tokens, hidden)`` tensor at the same time:
        - Weights:           ~5 GB
        - Per-batch concat:  ~B × 1.76 GB (empirical, from B=10 OOM)
        - Per-batch states:  ~B × 1.76 GB (lives until concat returns)
        - Total:  ~5 + 2 × B × 1.76 ≈ 5 + 3.5×B GB
        - B=4:    ~19 GB — original baseline
        - B=8:    ~33 GB — default, ~6 GB margin
        - B=10:   ~40 GB — OOM on the concat (~17.6 GB allocation)
        - B=18+:  OOMs by larger margins
        To go above B=8 on A100-40GB without buying more memory, the
        upstream ``predict_hidden_states`` would need a separate
        monkey-patch that subselects layers before the concat. On
        H100-80GB / A100-80GB, B=27 (single forward) is feasible.

        Frame I/O (moviepy seek+decode) is left as-is — the bottleneck
        we're targeting is the GPU forward, and batching frame I/O is a
        separate change (G in DESIGN.md §15).

        Disable by setting ``TRIBE_VIDEO_BATCH=0`` (or 1) in the env.

        **Policy note.** This intentionally violates the
        "don't touch tribev2 internals" rule from DESIGN.md §15
        (2026-04-25). Re-enabled by user request once the rest of the
        pipeline was stable. If neuralset upgrades break the import or
        the method signature, the import below raises at startup —
        loud failure, easy to spot.
        """
        batch_size = int(os.getenv("TRIBE_VIDEO_BATCH", "8"))
        if batch_size <= 1:
            log.info(
                "TRIBE_VIDEO_BATCH=%d (≤1) → skipping batched-encoding patch; "
                "neuralset.extractors.video.HuggingFaceVideo._get_data left as upstream",
                batch_size,
            )
            return

        import numpy as np  # noqa: WPS433
        import torch  # noqa: WPS433
        from tqdm import tqdm  # noqa: WPS433
        from neuralset import base as nsbase  # type: ignore  # noqa: WPS433
        from neuralset.extractors.video import (  # type: ignore  # noqa: WPS433
            HuggingFaceVideo,
            _HFVideoModel,
            _VideoImage,
            logger as upstream_logger,
        )

        def _patched_get_data(self, events):
            # Image-model path (DINOv2 etc.) unchanged — only the video-model
            # branch has the B=1 bottleneck we're fixing.
            if not any(z in self.image.model_name for z in _HFVideoModel.MODELS):
                yield from self._get_data_from_image_model(events)
                return

            # ── Per-event cache (mimics upstream @infra.apply behaviour) ──
            # Without this, the upstream caller pattern breaks our patch:
            #   1. Main process: _get_data called from "Preparing extractor:
            #      video" (this is the main batched encoding call).
            #   2. Main process: builds DataLoader, forks workers.
            #   3. Worker process: re-calls _get_data per dataset item,
            #      tries to re-instantiate _HFVideoModel and move it to
            #      GPU → PyTorch raises "Cannot re-initialize CUDA in
            #      forked subprocess".
            # Caching per-event in main process before the fork lets workers
            # short-circuit on the cache hit (memory is COW-inherited by
            # the forked children, so cache reads are free across fork).
            #
            # Cache key is content-based (filepath + offset + duration +
            # start) rather than calling ``event._splittable_event_uid()``
            # because that method was added in neuralset>=0.1.0 and tribev2
            # pins neuralset==0.0.2 today. The content key is stable
            # across processes (both main and worker see the same string)
            # and works on any neuralset version.
            def _key(event):
                return (
                    getattr(event, "filepath", None),
                    getattr(event, "offset", None),
                    getattr(event, "duration", None),
                    getattr(event, "start", None),
                )

            cache = getattr(self, "_aesthesis_video_cache", None)
            if cache is None:
                cache = {}
                self._aesthesis_video_cache = cache  # type: ignore[attr-defined]

            uncached = [e for e in events if _key(e) not in cache]

            # Only construct the model if we actually need to compute.
            # In a forked worker the cache is fully populated, so this
            # block is skipped and we never touch CUDA.
            if uncached:
                model = _HFVideoModel(
                    model_name=self.image.model_name,
                    pretrained=self.image.pretrained,
                    layer_type=self.layer_type,
                    num_frames=self.num_frames,
                )
                if model.model.device.type == "cpu":
                    model.model.to(self.image.device)

                # subtimes computed once (matches upstream: they use
                # events[0].frequency outside the per-event loop).
                freq0 = events[0].frequency if self.frequency == "native" else self.frequency
                T0 = 1 / freq0 if self.clip_duration is None else self.clip_duration
                subtimes = [
                    k / model.num_frames * T0
                    for k in reversed(range(model.num_frames))
                ]

                for event in uncached:
                    video = event.read()
                    # Per-event freq used for expect_frames + the output's
                    # TimedArray.frequency. Matches upstream's recomputation.
                    freq = self.frequency if self.frequency != "native" else event.frequency
                    expect_frames = nsbase.Frequency(freq).to_ind(event.duration)
                    upstream_logger.debug(
                        "Loaded Video (duration %ss at %sfps, shape %s):\n%s",
                        video.duration, video.fps, tuple(video.size), event.filepath,
                    )
                    times = np.linspace(0, video.duration, expect_frames + 1)[1:]
                    n_chunks = len(times)

                    # ── Phase 1: per-chunk frame I/O via moviepy (sequential) ──
                    # Pre-allocate one big ndarray rather than np.stack of a list,
                    # to halve peak memory during chunk assembly.
                    chunks = None
                    for k, t in enumerate(times):
                        ims = [_VideoImage(video=video, time=max(0, t - t2))
                               for t2 in subtimes]
                        pil_imgs = [i.read() for i in ims]
                        if pil_imgs and self.max_imsize is not None:
                            factor = max(pil_imgs[0].size) / self.max_imsize
                            if factor > 1:
                                size = tuple(int(s / factor) for s in pil_imgs[0].size)
                                pil_imgs = [pi.resize(size) for pi in pil_imgs]
                        data = np.array([np.array(pi) for pi in pil_imgs])
                        if chunks is None:
                            chunks = np.zeros((n_chunks,) + data.shape, dtype=data.dtype)
                        chunks[k] = data
                    upstream_logger.debug(
                        "Assembled %d chunks of shape %s for batched V-JEPA forward "
                        "(batch_size=%d)",
                        n_chunks, chunks.shape[1:], batch_size,
                    )

                    # ── Phase 2: batched GPU forward ──
                    output = None
                    for i in tqdm(
                        range(0, n_chunks, batch_size),
                        desc=f"Encoding video (B={batch_size})",
                    ):
                        batch = chunks[i:i + batch_size]
                        # predict_hidden_states accepts batched input via
                        # `kwargs[field] = list(images)` — list of B videos,
                        # each (num_frames, H, W, 3). The B=1 enforcement
                        # is in the *caller* (upstream _get_data), not here.
                        t_embds = model.predict_hidden_states(batch, audio=None)
                        # t_embds: (B, n_layers, n_tokens, embed_dim) on GPU.
                        # At B=18 vitg this is ~33 GB; freeing it explicitly
                        # before the next batch is critical, otherwise the
                        # next forward's allocation overlaps with the
                        # still-alive previous tensor and OOMs.
                        for b in range(t_embds.shape[0]):
                            t_embd = t_embds[b]
                            embd = self.image._aggregate_tokens(t_embd).cpu().numpy()
                            if self.image.cache_n_layers is None:
                                embd = self.image._aggregate_layers(embd)
                            if output is None:
                                output = np.zeros((n_chunks,) + embd.shape)
                                upstream_logger.debug(
                                    "Created Tensor with size %s", output.shape,
                                )
                            output[i + b] = embd
                        # Free the batch's GPU tensor before computing the
                        # next batch. Without this, Python rebinds `t_embds`
                        # only AFTER the next predict_hidden_states finishes
                        # allocating, so peak memory is 2× the per-batch
                        # tensor.
                        del t_embds, t_embd
                        torch.cuda.empty_cache()

                    video.close()
                    # Move time-axis to the back, matching upstream's contract.
                    output = output.transpose(list(range(1, output.ndim)) + [0])
                    cache[_key(event)] = nsbase.TimedArray(
                        data=output.astype(np.float32),
                        frequency=freq,
                        start=nsbase._UNSET_START,
                        duration=event.duration,
                    )

                # Free the V-JEPA model + any residual GPU state before
                # yielding. Without this, the encoder's ~5 GB of weights
                # plus PyTorch's cached allocations stay resident through
                # the prediction phase, where TRIBE's own forward pass
                # needs ~17 GB of free GPU memory and OOMs. Without this
                # del-and-empty_cache, even small batch sizes fail in
                # the prediction stage rather than encoding.
                del model
                torch.cuda.empty_cache()

            # Yield from cache for all requested events. Cache is fully
            # populated by this point (either pre-existing, or just
            # filled by the loop above).
            for event in events:
                yield cache[_key(event)]

        HuggingFaceVideo._get_data = _patched_get_data  # type: ignore[assignment]
        log.info(
            "patched neuralset.extractors.video.HuggingFaceVideo._get_data "
            "→ batched V-JEPA forward (BATCH_SIZE=%d). Set TRIBE_VIDEO_BATCH=0 to disable.",
            batch_size,
        )

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
        # Audio is neutralised by physically stripping the audio stream
        # from the MP4 at the request boundary (api._strip_audio_track).
        # With no audio stream, tribev2.ExtractAudioFromVideo produces
        # zero Audio events and the text/audio extractors get pruned by
        # tribev2.main with "Removing extractor … as there are no
        # corresponding events".
        #
        # Speed: V-JEPA chunk encoding is the dominant cost. We monkey-
        # patch HuggingFaceVideo._get_data to run B=N forwards instead of
        # B=1. The patch attaches to the class, so it must run *before*
        # TribeModel.from_pretrained instantiates the extractor.
        self._patch_video_extractor_for_batching()
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
