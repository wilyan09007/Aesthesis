"""End-to-end smoke test.

Spins up the TRIBE FastAPI app in-process (mock mode), points the
Aesthesis app at it via a wrapping fixture, then POSTs a fake MP4 to
`/api/analyze` and verifies the response shape.

This is the load-bearing test. If it passes, the entire backend pipeline
is wired correctly:
    upload -> validate (header-only fallback) -> TRIBE mock inference ->
    ROI extract -> timeline build -> events -> screenshots (best effort) ->
    Gemini mock synthesis -> aggregate metrics -> verdict -> response.

Marked `integration` so CI can run it on demand (`pytest -m integration`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# These imports must succeed even without ffmpeg / google-generativeai —
# both the validation layer and the synthesizer have built-in fallbacks.
from aesthesis.config import AppConfig
from aesthesis.orchestrator import run_analysis


pytestmark = pytest.mark.integration


@pytest.fixture
def fake_mp4(tmp_path: Path) -> tuple[Path, Path]:
    """Two non-empty 'MP4-shaped' files. The mock TRIBE runner doesn't
    actually decode them — it probes for duration via ffmpeg if available
    and otherwise falls back to a default n_TRs."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"\x00\x01" * 4096)
    b.write_bytes(b"\x00\x02" * 4096)
    return a, b


@pytest.fixture
def tribe_server(monkeypatch):
    """Start the TRIBE FastAPI app in-process and yield its base URL.

    Uvicorn would let us run a real HTTP server; for tests we use an in-
    process httpx ASGI transport so there's no port juggling.
    """
    from tribe_neural.api import app as tribe_app

    yield "asgi://tribe", tribe_app


@pytest.fixture
def patched_client(monkeypatch, tribe_server):
    """Monkey-patch TribeClient.process_video_timeline to talk to the
    in-process ASGI app instead of an HTTP server."""
    base_url, asgi_app = tribe_server
    import httpx
    from aesthesis import tribe_client as tc

    real_init = tc.TribeClient.__init__
    real_call = tc.TribeClient.process_video_timeline

    def init(self, *args, **kwargs):
        real_init(self, base_url, **{k: v for k, v in kwargs.items() if k == "timeout_s"})

    async def call(self, video_path: Path, *, window_trs=4, step_trs=1, run_id):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi_app),
            base_url=base_url,
            timeout=30,
        ) as client:
            with video_path.open("rb") as fh:
                files = {"video": (video_path.name, fh, "video/mp4")}
                data = {"window_trs": str(window_trs), "step_trs": str(step_trs),
                        "run_id": run_id}
                r = await client.post("/process_video_timeline", files=files, data=data)
                r.raise_for_status()
                return r.json()

    monkeypatch.setattr(tc.TribeClient, "__init__", init)
    monkeypatch.setattr(tc.TribeClient, "process_video_timeline", call)
    yield


async def test_analyze_smoke(fake_mp4, patched_client, monkeypatch):
    a, b = fake_mp4
    monkeypatch.setenv("GEMINI_MOCK_MODE", "1")
    monkeypatch.setenv("TRIBE_MOCK_MODE", "1")
    monkeypatch.setenv("CLEANUP_UPLOADS", "0")

    cfg = AppConfig()
    response = await run_analysis(
        cfg=cfg, video_a=a, video_b=b,
        goal="evaluate signup flow", run_id="test-run-1",
    )

    # Top-level shape
    assert response.meta.run_id == "test-run-1"
    assert response.meta.goal == "evaluate signup flow"
    assert response.mock is True

    # Per-version
    for v, side in (("A", response.a), ("B", response.b)):
        assert side.version == v
        assert side.timeline.n_trs > 0
        # 8 ROIs
        assert set(side.timeline.roi_series.keys()) == {
            "aesthetic_appeal", "visual_fluency", "cognitive_load",
            "trust_affinity", "reward_anticipation", "motor_readiness",
            "surprise_novelty", "friction_anxiety",
        }
        # Composite series should have all 8 keys
        assert set(side.timeline.composites_series.keys()) >= {
            "appeal_index", "conversion_intent", "fluency_score", "trust_index",
            "engagement_depth", "surprise_polarity", "memorability_proxy",
            "ux_dominance",
        }
        # Mock synthesizer emits one insight per event
        assert len(side.insights) == len(side.events)
        for ins in side.insights:
            assert ins.version == v
            assert ins.cited_brain_features  # non-empty per §4.5 hard constraint

    # Aggregate metrics — eight named comparisons
    assert {m.name for m in response.aggregate_metrics} >= {
        "mean_appeal_index", "mean_cognitive_load",
        "pct_reward_dominance", "pct_friction_dominance",
        "friction_spike_count", "motor_readiness_peak",
        "flow_state_windows", "bounce_risk_windows",
    }
    for m in response.aggregate_metrics:
        assert m.edge in ("A", "B", "tie")

    # Verdict
    assert response.verdict.winner in ("A", "B", "tie")
    assert response.verdict.summary_paragraph
    assert response.verdict.decisive_moment
