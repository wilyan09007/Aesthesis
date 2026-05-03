"""Bbox coercion + PIL overlay tests — real Pillow, no mocks.

Mirrors the rest of the app suite's posture (``feedback_no_mocks``):
real binaries, real I/O, fail loudly when the env isn't set up. If
Pillow isn't installed the tests refuse to skip — they fail with an
actionable message pointing at requirements-app.txt.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from aesthesis import annotate
from aesthesis.annotate import _coerce_bbox, annotate_to_b64_jpeg


def _require_pillow():
    try:
        import PIL  # type: ignore  # noqa: F401
    except ImportError:
        pytest.fail(
            "Pillow not installed — install via `pip install -r "
            "requirements-app.txt`. annotate.py is the prod path; "
            "refusing to skip."
        )


def _make_jpeg(out: Path, size=(800, 600), color=(40, 40, 60)) -> Path:
    _require_pillow()
    from PIL import Image
    img = Image.new("RGB", size, color)
    img.save(out, "JPEG", quality=85)
    return out


# ─── _coerce_bbox: every branch ──────────────────────────────────────────────


def test_coerce_bbox_passes_through_valid_normalised():
    assert _coerce_bbox([0.1, 0.2, 0.4, 0.5]) == (0.1, 0.2, 0.4, 0.5)


def test_coerce_bbox_handles_0_1000_convention():
    """Gemini occasionally returns coords in 0..1000. We rescale silently."""
    res = _coerce_bbox([100.0, 200.0, 400.0, 500.0])
    assert res is not None
    x0, y0, x1, y1 = res
    assert x0 == pytest.approx(0.1, abs=1e-6)
    assert y0 == pytest.approx(0.2, abs=1e-6)
    assert x1 == pytest.approx(0.4, abs=1e-6)
    assert y1 == pytest.approx(0.5, abs=1e-6)


def test_coerce_bbox_clamps_tiny_overshoots():
    """Gemini sometimes returns 1.01 / -0.005 — clamp those silently to
    [0, 1] rather than dropping the box."""
    res = _coerce_bbox([-0.01, 0.0, 1.01, 1.0])
    assert res is not None
    x0, y0, x1, y1 = res
    assert 0.0 <= x0 <= 1.0
    assert 0.0 <= x1 <= 1.0


def test_coerce_bbox_drops_unsalvageable_negative():
    """Negative coords beyond the small clamp tolerance — can't be
    salvaged by the 0..1000 rescale either (negatives × /1000 stay
    negative). Drop entirely rather than draw on a wrong region."""
    assert _coerce_bbox([-0.5, 0.1, 0.4, 0.5]) is None


def test_coerce_bbox_drops_when_rescale_still_out_of_range():
    """Values that exceed even the 0..1000 convention's range (e.g. 1500+
    pixels for a normalised box) fail the post-rescale clamp and drop."""
    # /1000 → (1.5, 1.8, 2.2, 2.5), still way out of [0, 1.02].
    assert _coerce_bbox([1500.0, 1800.0, 2200.0, 2500.0]) is None


def test_coerce_bbox_drops_degenerate():
    """x1<=x0 or y1<=y0 produces no visible rectangle — drop it."""
    assert _coerce_bbox([0.4, 0.2, 0.4, 0.5]) is None
    assert _coerce_bbox([0.4, 0.5, 0.5, 0.4]) is None


def test_coerce_bbox_drops_wrong_length():
    assert _coerce_bbox([0.1, 0.2, 0.3]) is None
    assert _coerce_bbox([]) is None


def test_coerce_bbox_drops_non_numeric():
    assert _coerce_bbox(["a", 0.2, 0.3, 0.4]) is None


def test_coerce_bbox_handles_none():
    assert _coerce_bbox(None) is None


# ─── annotate_to_b64_jpeg: real PIL, real bytes ──────────────────────────────


def test_annotate_returns_base64_jpeg(tmp_path: Path):
    src = _make_jpeg(tmp_path / "src.jpg")
    res = annotate_to_b64_jpeg(src, [0.2, 0.2, 0.6, 0.5])
    assert res is not None and isinstance(res, str)
    decoded = base64.b64decode(res)
    # JPEG magic bytes — proves the output is a real JPEG, not garbage.
    assert decoded[:3] == b"\xff\xd8\xff", (
        "annotate_to_b64_jpeg did not produce a JPEG byte stream"
    )
    # Round-trip through PIL to prove the file is still openable after
    # the bbox draw.
    from PIL import Image
    img = Image.open(io.BytesIO(decoded))
    assert img.size == (800, 600)


def test_annotate_returns_none_for_missing_screenshot(tmp_path: Path):
    res = annotate_to_b64_jpeg(tmp_path / "no_such.jpg", [0.1, 0.1, 0.5, 0.5])
    assert res is None


def test_annotate_returns_none_for_unsalvageable_bbox(tmp_path: Path):
    """End-to-end: a wholly-out-of-range bbox produces no overlay rather
    than drawing on a wrong region."""
    src = _make_jpeg(tmp_path / "src.jpg")
    res = annotate_to_b64_jpeg(src, [-0.8, 0.1, 0.4, 0.5])
    assert res is None


def test_annotate_handles_0_1000_bbox(tmp_path: Path):
    """End-to-end coercion: 0..1000 input still produces a valid overlay."""
    src = _make_jpeg(tmp_path / "src.jpg")
    res = annotate_to_b64_jpeg(src, [200, 200, 600, 500])
    assert res is not None
    decoded = base64.b64decode(res)
    assert decoded[:3] == b"\xff\xd8\xff"


def test_annotate_pillow_required_in_dev_env():
    """The dev env MUST have Pillow available — otherwise the prod path
    silently degrades to "no annotated screenshots" and the regression
    is invisible. Refuses to skip."""
    _require_pillow()
