<div align="center">

# 🧠 Aesthesis: *Film your demo. Watch the brain react.*

[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-E0454D?style=flat-square)](./CHANGELOG.md)
[![Powered by TRIBE v2](https://img.shields.io/badge/Powered%20by-TRIBE%20v2-555?style=flat-square)](https://github.com/facebookresearch/tribev2)

</div>

---

## The Problem

Knowing whether a product *feels good* is hard. Surveys lie politely. A/B tests tell you which variant won, never why. Usability panels of five strangers cost a week and a four-figure invoice — per round. Tools like Maze, UserTesting, and Hotjar show you click-through rates, heatmaps, and post-hoc questionnaires, but they all share a fatal limitation: the user is the sensor, and the user is unreliable.

The brain has already decided in 200 ms. Nobody is reading it.

---

## What Aesthesis Does ✨

Drop in a 30-second screen recording. That's it.

Aesthesis predicts the viewer's neural response *per second* across eight UX-relevant brain signals, mines the timeline for the moments that matter, and turns each one into a timestamped recommendation. Six to thirteen seconds later, you get a real cortical mesh pulsing to the activity, an interactive timeline, and an overall verdict on how the experience lands.

- 🎥 **Upload once** — any screen recording, any product, ≤30 s
- 🧠 **Eight brain signals** — appeal, fluency, cognitive load, trust, reward, motor readiness, surprise, friction
- 🌐 **Real fsaverage5 cortex** — both hemispheres, per-face activity, drag to rotate
- ⏱️ **~7–13 s end-to-end** — TRIBE v2 on GPU, Gemini for narration, your browser for the rest

> The synthetic subject isn't a feature — it's the product.

---

## Demo 🎬

Live walkthrough: **[aesthesis-frontend.vercel.app](https://aesthesis-frontend.vercel.app)**

![alt text](image-1.png)

Aesthesis uses **Meta's TRIBE v2** to predict per-vertex cortical response from video, **Google Gemini** to translate neural events into UX language, and a **real-time three.js cortical mesh** to render every TR onto an inflated fsaverage5 brain. What you see is not a stylized animation — every face on that mesh is colored from a real model prediction, sampled every 1.5 seconds.

---

## Why This Matters

The UX research market is enormous — companies spend billions a year asking users questions, watching them click, and inferring intent from incomplete signal. The bottleneck has never been *more* data. It's always been *better* data.

Aesthesis closes the gap. More importantly, it changes *what* product feedback can feel like. Most evaluation tools are post-hoc dashboards: green numbers, red numbers, deltas you stare at on Monday. Aesthesis gives you a brain. Watching the cortex light up to your hero section is qualitatively different from reading a CTR — it builds intuition, exposes the moments that survey questions can't reach, and makes "this part feels off" a measurable claim instead of a vibe.

---

## Try It Out 🚀

```bash
git clone <repo> && cd Aesthesis
cp .env.example .env   # fill in TRIBE_SERVICE_URL, GEMINI_API_KEY, etc.
./dev.sh               # macOS · Linux · Git-Bash on Windows
```

Open `http://localhost:3000`, drop an MP4, and watch the cortex come alive.

For production: `modal deploy tribe_service/modal_app.py`, `modal deploy aesthesis_app/modal_app.py`, push frontend to Vercel.

---

## How We Built It 🛠️

### The TRIBE Pipeline

In 2025, Meta open-sourced TRIBE v2 — a transformer trained on 80 hours of fMRI from people watching naturalistic video. Given a clip, it predicts brain response across 20,484 cortical vertices, sampled every 1.5 s. We wrap it as an HTTP service on Modal (A100-40GB), strip the audio at the request boundary so the audio extractors never load, and pre-bake V-JEPA-2 weights into the container image so cold starts don't pay a 5 GB HuggingFace download tax. End-to-end inference on a 30 s clip: 3–8 seconds warm.

### Eight UX-Tuned Signals

Raw vertex activity is unreadable. We project it onto eight brain signals tuned for product experience — Yeo network masks intersected with NeuroSynth meta-analytic weight maps. Each one is a real network. Each one reads in a single English sentence. They feed five composite indices and seven Pearson connectivity pairs.

| Signal | Reads as | Built from |
|---|---|---|
| `aesthetic_appeal` | I like looking at this. | Default Mode + Limbic, `memory` × `reward` |
| `visual_fluency` | I see it cleanly. | Visual cortex |
| `cognitive_load` | I'm working harder than I want to. | Control + DorsAttn, `conflict` × `uncertainty` |
| `trust_affinity` | This feels safe. | Default Mode, `social` |
| `reward_anticipation` | I want to click that. | Limbic, `reward` |
| `motor_readiness` | My hand wants to move. | Somatomotor, `motor` |
| `surprise_novelty` | Wait — what was that? | Salience + Control |
| `friction_anxiety` | Something is wrong. | Salience, `fear` |

### Event Mining + Gemini Narration

A deterministic miner walks the per-TR timeline and extracts seven event types — spikes, dominant shifts, sustained dominance, co-movements, troughs, flow windows, bounce-risk windows — capping at 15 per minute and keeping the highest-magnitude events with class diversity. Each event ships to Gemini with the screenshot of that exact frame. A second Gemini call generates the overall assessment from aggregate metrics. Insights are clamped to video bounds — no more cards pointing past the end of the player.

### Real Cortical Mesh

Every face on the brain panel is colored from a uint8 RGB stream baked by the GPU worker (Schaefer-400 atlas projected onto fsaverage5). The frontend loads two GLBs — left + right hemispheres, inflated and pial variants — and runs a custom three.js shader with HDR emissive boost, fresnel rim glow, ACES filmic tone mapping, and a real UnrealBloomPass. Front/back face split per hemisphere kills painter's-algorithm sort glitches. Smoothstep on alpha hides the discrete TR cadence. It's not @react-three/fiber — every per-frame paint is hand-rolled three.js because we needed control over the render order.

### Saved Runs + Agent Conversations

Runs save to Neon Postgres via Prisma. Auth0 handles login. The agent panel is an Anthropic-powered Claude assistant scoped to your run history through Backboard — "compare this run with my best past one" returns numbers, not vibes. Threads persist per run; ephemeral threads back unsaved sessions.

### Tech Stack

| Layer | Technology |
|---|---|
| Neural model | Meta TRIBE v2 + V-JEPA-2-vitg-fpc64-256 |
| Resource generation | NeuroSynth · NiMARE · nilearn · Schaefer-400 atlas |
| GPU service | FastAPI · ARQ · Modal · A100-40GB · `decord` for batched decoding |
| Orchestrator | FastAPI · `httpx` · `ffmpeg-python` · Pydantic · Modal CPU |
| Insight LLM | Google Gemini 2.0 Flash (insights + assessment) |
| Frontend | Next.js 16 · React 19 · Tailwind 4 · framer-motion · recharts |
| Cortical mesh | three.js (hand-rolled) · GLTFLoader · UnrealBloom · OrbitControls |
| Persistence | Neon Postgres · Prisma 6 |
| Auth | Auth0 |
| Conversational agent | Anthropic Claude · Backboard · Vercel AI SDK |

**Hosted on:** Modal (×2) · Vercel · Neon

---

## Challenges We Overcame 💪

**The 2:30 audio bottleneck.** TRIBE v2's first transform tries to extract audio for WhisperX transcription — a sub-process that took 2–3 minutes on a 30-second clip and dwarfed the actual GPU inference. We strip the audio track with `ffmpeg -an -c:v copy` at the request boundary, which causes tribev2's pipeline to prune the audio and text extractors before any weights load. End-to-end TRIBE time dropped from minutes to seconds.

**Custom GLB attributes surviving GLTFLoader.** The cortical mesh needs a per-vertex parcel ID and a sulcal-depth value baked into the GLB. three.js's GLTFLoader strips unknown attributes by default. We hand-built the buffer/bufferView/accessor graph in `pygltflib` with underscore-prefixed names (`_PARCELID`, `_SULC`) — the only naming convention that survives the loader. Without that, the brain renders flat.

**V-JEPA fragmentation OOMs at B=20.** PyTorch's CUDA allocator was leaving ~8 GB "reserved but unallocated" between batched forward passes on a 40 GB A100, and the next forward would OOM with 306 MiB free. Setting `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` *before* `torch` is imported fixed it permanently. The order of those two lines is load-bearing.

---

## What We Learned 📚

**Visible AI changes belief.** The first time you watch the cortex pulse to your own product video, the vibe shifts. Numbers and dashboards make people skeptical; a brain reacting in front of them makes them lean forward. The visualization isn't decoration — it's the trust layer.

**No mocks, ever.** This codebase used to have mocked tests for the pipeline. They passed happily through a broken migration that mocked away the real failure. Tests now hit real TRIBE on real GPU and real Gemini on real keys. They fail loudly when the env isn't there. The bill is real; the signal is real.

**Honesty in the README compounds.** `DESIGN.md` describes a Phase 2 capture pipeline (paste a URL, BrowserUse drives Chromium, watch the agent live) that isn't built yet. We say so out loud. Code is what runs; documents are what we hope.

---

## What's Next 🔭

- **Phase 2 capture pipeline** — paste a URL, BrowserUse drives Chromium, the live screencast streams into the analyzer (planned in `DESIGN.md` §4.2 + `ASSUMPTIONS_PHASE2_CAPTURE.md`)
- **Subcortical extension** — TRIBE v2 also predicts NAcc, amygdala, anterior hippocampus, VTA. We haven't exposed them yet
- **Parcel-subset masks** for `trust_affinity` / `aesthetic_appeal` / `surprise_novelty` — published anatomy wants tighter subsets than v1's whole-network masks
- **Multi-run comparisons in the UI** — the data is in Postgres, the agent can already answer questions about it; a dedicated comparison view is the next frontier
- **Mobile capture** — record a flow on your phone, analyze instantly

---

<div align="center">

*The brain is always watching. Aesthesis just writes down what it saw.*

</div>
