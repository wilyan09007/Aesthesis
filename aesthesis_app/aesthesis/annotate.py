"""Bounding-box annotation overlay for per-event screenshots.

Given a screenshot and a Gemini-emitted ``bbox_norm`` ([x0, y0, x1, y1]
in 0..1 of the screenshot pixel space), produce a JPEG with the box
drawn on it. The annotated JPEG ships base64-encoded on the
``Insight.annotated_screenshot_b64`` field — the frontend renders it
inline; the agent prompt embeds it as a Markdown data URI when feasible.

Bbox coercion (ASSUMPTIONS_AGENT_PROMPT.md §5.3 + §21.2):
- Gemini sometimes ships bbox in 0..1000 range (legacy detection
  convention). If any coord is > 1.5 we divide all four by 1000 and try
  again.
- After coercion, if any coord is still out of [0, 1], we drop the
  overlay (return ``None`` in place of the annotated image) rather than
  draw a wrong box. A wrong box is worse than no box — the prompt
  degrades gracefully and the agent searches by descriptors instead.

Verbose logging at every decision boundary — bbox decisions are
load-bearing for prompt quality and a wrong-box rate above ~5% should
surface in the orchestrator's INFO log without grepping per-event
debug noise.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


#: Aesthesis accent colour. Matches UIUX.md §3.1.
_BBOX_RGB: tuple[int, int, int] = (224, 69, 77)


def _coerce_bbox(
    bbox: Sequence[float] | None,
    *,
    run_id: str | None = None,
    insight_idx: int | None = None,
) -> tuple[float, float, float, float] | None:
    """Normalise a Gemini bbox to floats in [0, 1] or return None.

    Returns the coerced 4-tuple or ``None`` if the box can't be salvaged.
    Logs every coercion path so a wrong-box regression is visible.
    """
    if bbox is None:
        return None
    if len(bbox) != 4:
        log.info(
            "bbox dropped (wrong length=%d)", len(bbox),
            extra={"step": "annotate.coerce", "run_id": run_id,
                   "insight_idx": insight_idx},
        )
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError) as e:
        log.info(
            "bbox dropped (non-numeric: %s)", e,
            extra={"step": "annotate.coerce", "run_id": run_id,
                   "insight_idx": insight_idx},
        )
        return None

    raw = (x0, y0, x1, y1)
    # Detect 0..1000 convention and rescale.
    if max(abs(v) for v in raw) > 1.5:
        log.debug(
            "bbox in 0..1000 space — rescaling",
            extra={"step": "annotate.coerce", "run_id": run_id,
                   "insight_idx": insight_idx, "raw": raw},
        )
        x0, y0, x1, y1 = (v / 1000.0 for v in raw)

    # Clamp tiny overshoots [-eps, 1+eps] back into [0, 1].
    if all(-0.02 <= v <= 1.02 for v in (x0, y0, x1, y1)):
        x0 = max(0.0, min(1.0, x0))
        y0 = max(0.0, min(1.0, y0))
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))
    else:
        log.info(
            "bbox dropped (out of [0,1] after coerce): %s",
            (x0, y0, x1, y1),
            extra={"step": "annotate.coerce", "run_id": run_id,
                   "insight_idx": insight_idx},
        )
        return None

    # Reject degenerate / inverted boxes.
    if x1 <= x0 or y1 <= y0:
        log.info(
            "bbox dropped (degenerate or inverted): %s",
            (x0, y0, x1, y1),
            extra={"step": "annotate.coerce", "run_id": run_id,
                   "insight_idx": insight_idx},
        )
        return None

    return (x0, y0, x1, y1)


def annotate_to_b64_jpeg(
    screenshot_path: Path,
    bbox_norm: Sequence[float] | None,
    *,
    quality: int = 82,
    run_id: str | None = None,
    insight_idx: int | None = None,
) -> str | None:
    """Draw ``bbox_norm`` on the screenshot and return base64 JPEG bytes.

    Returns ``None`` when:
      - The screenshot file is missing / unreadable.
      - PIL is not installed.
      - The bbox can't be salvaged by ``_coerce_bbox``.

    The fallback is intentional — the agent prompt still works without
    the annotated image (it just degrades to text descriptors).
    """
    if not screenshot_path or not screenshot_path.exists():
        log.debug(
            "annotate skipped — screenshot missing",
            extra={"step": "annotate", "run_id": run_id,
                   "insight_idx": insight_idx,
                   "path": str(screenshot_path) if screenshot_path else None},
        )
        return None

    coerced = _coerce_bbox(bbox_norm, run_id=run_id, insight_idx=insight_idx)
    if coerced is None:
        return None

    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        log.warning(
            "Pillow not installed — annotated screenshots disabled. "
            "`pip install Pillow` to enable.",
            extra={"step": "annotate", "run_id": run_id},
        )
        return None

    try:
        with Image.open(screenshot_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            x0, y0, x1, y1 = coerced
            px0, py0 = int(x0 * width), int(y0 * height)
            px1, py1 = int(x1 * width), int(y1 * height)

            draw = ImageDraw.Draw(img, "RGBA")
            # Solid 4px stroke + soft glow halo. The halo reads on both
            # dark and light pages without obscuring the element.
            draw.rectangle(
                [px0, py0, px1, py1],
                outline=(*_BBOX_RGB, 255),
                width=4,
            )
            for offset, alpha in ((2, 96), (5, 48)):
                draw.rectangle(
                    [px0 - offset, py0 - offset, px1 + offset, py1 + offset],
                    outline=(*_BBOX_RGB, alpha),
                    width=2,
                )

            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
    except OSError as e:
        log.info(
            "annotate failed (PIL OSError: %s) — dropping overlay",
            e,
            extra={"step": "annotate", "run_id": run_id,
                   "insight_idx": insight_idx,
                   "path": str(screenshot_path)},
        )
        return None
    except Exception as e:  # noqa: BLE001
        log.warning(
            "annotate failed unexpectedly (%s: %s) — dropping overlay",
            type(e).__name__, e,
            extra={"step": "annotate", "run_id": run_id,
                   "insight_idx": insight_idx},
        )
        return None

    encoded = base64.b64encode(data).decode("ascii")
    log.debug(
        "annotated screenshot ready",
        extra={"step": "annotate", "run_id": run_id,
               "insight_idx": insight_idx,
               "bytes_in": screenshot_path.stat().st_size,
               "bytes_out": len(data)},
    )
    return encoded
