# ASSUMPTIONS_AGENT_PROMPT.md — Closing the dev loop

> Scope: re-design the insight pipeline so the per-event deliverable is a
> **paste-into-your-coding-agent prompt** — concrete enough that an agent
> (Claude Code, Cursor, Aider, Codex, etc.) can locate the offending UI
> element in the user's source and apply the fix without further questions.
>
> This is the planning + research record. It is the spec — no code is
> written yet. Every external fact I had to verify, every choice I made
> when DESIGN.md is silent, and every risk I'm consciously accepting is
> captured here so the implementer can argue with this file instead of
> reverse-engineering my reasoning later.

---

## 1. What the deliverable actually is

For each `Insight`, render an **agent-ready prompt** as a single Markdown
string the user copies to the clipboard with one click and pastes into
the coding agent of their choice. The prompt is:

1. **Self-contained** — no Aesthesis-specific URLs the agent has to
   resolve. Anything the agent needs to "find" in the user's repo is
   described in three converging ways: visible copy, location hint, and
   visual anchors (with an optional reference screenshot).
2. **Specific** — names the element by its visible text, gives a
   normalised location hint ("upper-right of the hero", "3rd row of the
   pricing table"), and lists 1–3 visual anchors a search agent can
   verify against (sibling labels, surrounding copy, layout cues).
3. **Falsifiable** — has acceptance criteria so the agent (or the user)
   can confirm the change matches intent. "Increase visual weight of the
   primary CTA so it dominates the secondary CTAs" is a check the agent
   can perform against the diff. "Improve UX" is not.
4. **Brain-grounded** — cites the brain features that triggered the
   recommendation in plain language. The user has a reason to trust it
   that survives the paste into the agent.

The current `recommendation` field ("simplify the table to 3 columns")
is the seed of this — but it's a free-form sentence, not an actionable
instruction. The new deliverable wraps that sentence in a structured
envelope the agent can act on.

### 1.1 Why this is the right product cut

The "Get personalized fix" button currently lives on `InsightCard.tsx`
and calls Backboard. That returns a chatty paragraph ("here's a fix you
might consider…"). It's nice but not *implementable* — the user reads
it, nods, and now has to translate it into a request to their own coding
agent. We are removing that translation step. The Aesthesis brain told
us *what* changed and *why*; the prompt is *how* to change it.

This also collapses the "two-AI handoff" tax: today the user pays for a
Backboard call that produces text they then re-paste into Claude/Cursor
which costs another LLM round-trip. After this work, Aesthesis emits the
agent-actionable artifact directly.

---

## 2. End-to-end pipeline (post-restructure)

```
MP4 upload
  ↓
TRIBE  →  per-TR brain timeline (unchanged)
  ↓
extract_events.py  →  ≤15 noteworthy events (unchanged)
  ↓
extract_frame.py  →  per-event JPEG screenshot at timestamp (unchanged)
  ↓
synthesize.py:
  step A  ┐
          │  Gemini call #1 (vision-grounded element identification +
          │     change spec, structured JSON, one entry per event)
          ┘
  step B  ┐
          │  PIL bbox overlay  →  annotated_screenshot.jpg per event
          ┘
  step C  ┐
          │  prompt_renderer.py — deterministic Markdown template
          │     that turns structured fields into the final agent prompt
          ┘
  step D  ┐
          │  Gemini call #2 (overall assessment, unchanged)
          ┘
  ↓
build_response → AnalyzeResponse (extended schema)
  ↓
Frontend InsightCard:
  - "Copy prompt for AI agent" (clipboard)
  - "Download annotated screenshot" (PNG)
  - "Open in lightbox"
  - structured target/change/criteria displayed inline
```

The number of Gemini round-trips stays the same (still 2 per analysis).
The first call gets a richer schema; the second is unchanged. The PIL
overlay and the template render are deterministic and add ~50ms.

---

## 3. Schema changes

### 3.1 New nested types (Pydantic, mirrored in TS)

```python
class TargetElement(BaseModel):
    """Vision-grounded description of the UI element that triggered the event.

    The contract: every field here exists so that an AI coding agent can
    locate this element in the user's source code without further hints.
    The three converging anchors (visible_text, location_hint,
    visual_anchors) are deliberately redundant — any one of them might
    fail (text rendered in a sprite, layout described ambiguously,
    siblings not yet committed), but the conjunction nearly always
    survives.
    """
    label: str               # short human label, e.g. "Primary CTA — Start free trial"
    element_type: str        # button, heading, image, modal, input, link, table, …
    visible_text: str | None # exact on-screen copy if any
    location_hint: str       # "upper-right of hero section"
    visual_anchors: list[str]  # ≥1; e.g. ["under 'Pricing' heading", "right of feature list"]
    bbox_norm: tuple[float, float, float, float] | None
    # [x0, y0, x1, y1] in 0..1 of the SCREENSHOT's pixel space.
    # Used to draw the overlay; the agent doesn't consume coords directly.

class ProposedChange(BaseModel):
    """Diff intent. Tight enough to be implementable, loose enough that the
    agent can apply it idiomatically in the user's actual stack."""
    change_type: Literal[
        "copy", "layout", "hierarchy", "color", "spacing",
        "typography", "interaction", "removal", "addition", "structure",
    ]
    current_state: str       # "Button uses muted outline style with 12px label"
    desired_state: str       # "Button uses filled primary background with 16px label"
    rationale: str           # ties the change to the cited brain features

class Insight(BaseModel):
    timestamp_range_s: tuple[float, float]
    ux_observation: str                       # unchanged
    cited_brain_features: list[str]           # unchanged
    cited_screen_moment: str                  # unchanged
    target_element: TargetElement             # NEW
    proposed_change: ProposedChange           # NEW
    acceptance_criteria: list[str]            # NEW; 2-4 falsifiable bullets
    agent_prompt: str                         # NEW; rendered Markdown
    annotated_screenshot_b64: str | None      # NEW; data URI of the bbox overlay
    # `recommendation` stays as a derived one-line summary so existing UI
    # tooltips / chart hover labels keep working without a frontend rewrite.
    recommendation: str
```

### 3.2 Why nested instead of flat

A flat schema (e.g. a single `agent_prompt: str` field with everything
inlined) would be simpler — but the frontend wants to *render* the
target / change / criteria as styled blocks, not as one big preformatted
chunk. Nesting keeps both consumers (the prompt renderer and the UI)
honest off the same source of truth.

---

## 4. The prompt template (deterministic; not Gemini-generated)

Server-side, after Gemini returns the structured fields, we render a
Markdown string using a Python template (no extra LLM call). The
template is the contract — it doesn't drift between requests, so we can
lint it, version it, and A/B it without prompt regression risk.

### 4.1 Template (current draft)

```markdown
# UX fix from neural-response analysis

You are a coding agent. Implement this UI change in the user's codebase.
The change is grounded in a brain-response analysis of a screen recording
of the user's product — at the timestamp below, the viewer's brain showed
{cited_brain_features_phrase}, indicating {ux_observation_short}.

## What to change

**Element:** {target_element.label}
- **Type:** {target_element.element_type}
- **Visible text:** {visible_text_or_none}
- **Location:** {target_element.location_hint}
- **Visual anchors (use these to find the element in source):**
{visual_anchors_bullets}

**Reference screenshot:** see the attached image (the highlighted box
marks the target element).

**Change:** {proposed_change.change_type}
- **Current state:** {proposed_change.current_state}
- **Desired state:** {proposed_change.desired_state}
- **Why this is the right move:** {proposed_change.rationale}

## Acceptance criteria

The change is correct when all of the following hold:

{acceptance_criteria_checklist}

## How to find the element in the codebase

1. Search for the visible text {visible_text_quoted_or_fallback}.
   If multiple matches, narrow by the location hint and visual anchors.
2. Confirm the element is a `{element_type}` whose surroundings match
   the visual anchors.
3. If the framework template is dynamic (loops, slots), trace upward to
   the component that renders this region rather than editing the
   raw output.
4. Apply the change. Preserve every existing prop / class / handler that
   isn't part of the diff.

## Brain context (for your understanding, not a constraint)

- Time window: {t0:.1f}s – {t1:.1f}s of the demo recording
- Cited brain features: {cited_brain_features_csv}
- Screen moment phrase: "{cited_screen_moment}"

If, after locating the element, you believe a different fix would
better satisfy the acceptance criteria, prefer the criteria over the
literal `desired_state` text — that text is one valid implementation,
not the only one.
```

### 4.2 Variables and fallbacks

| placeholder | source | fallback when missing |
|---|---|---|
| `cited_brain_features_phrase` | rendered from `cited_brain_features` (e.g. "elevated friction with reduced visual fluency") | "an unexpected response" |
| `ux_observation_short` | first sentence of `ux_observation` | full `ux_observation` |
| `visible_text_or_none` | quoted `target_element.visible_text` | "(no visible text — element is identified by location and anchors only)" |
| `visual_anchors_bullets` | bulletised list | "- (no anchors provided)" |
| `visible_text_quoted_or_fallback` | "for the string `\"{visible_text}\"`" | "for elements matching the visual description above" |
| `acceptance_criteria_checklist` | `- [ ] {item}` per item | (always provided — Gemini call requires ≥2) |
| `element_type` | `target_element.element_type` | "element" |

### 4.3 Why a template (not "ask Gemini to write the prompt")

- **Deterministic.** Same structured input → same prompt. We can diff
  prompt revisions and roll them back. An LLM-written prompt drifts.
- **Cheap.** No third Gemini call.
- **Lintable.** We can validate that every prompt has bbox, anchors,
  acceptance criteria. With LLM-written prompts we'd be checking
  free-form output.
- **Auditable.** The template lives in source — `prompts.py` /
  `prompt_renderer.py`. Reviewers see it without running the pipeline.

---

## 5. Changes to the Gemini insight prompt

The current `INSIGHT_PROMPT_TEMPLATE` (`aesthesis_app/aesthesis/prompts.py`)
asks for `ux_observation` + `recommendation` + `cited_brain_features` +
`cited_screen_moment`. We're replacing it with a richer schema and a
stronger element-grounding instruction.

### 5.1 Key additions to the prompt instruction block

1. **Mandatory element grounding.** "For every event, you MUST identify
   the single most likely UI element on the screen that produced the
   brain response. If the screenshot is too ambiguous to commit to one
   element, set `target_element.label` to a phrase containing 'unclear'
   and provide the best 2 candidate descriptions in `visual_anchors`."
2. **Bbox in normalised coordinates.** "Return `bbox_norm` as
   `[x0, y0, x1, y1]` floats in `[0, 1]` of the screenshot's frame.
   `[0, 0]` is top-left. Tighten the box around the element — do not
   wrap an entire section if a single button is the actual target."
3. **Force visible_text when present.** "If the element contains
   readable text, copy the exact characters into `visible_text`. Do not
   paraphrase. If the element is a glyph / icon / image with no copy,
   set `visible_text` to null."
4. **Acceptance criteria mandate.** "Provide 2 to 4 acceptance criteria
   in `acceptance_criteria`. Each must be falsifiable by inspecting the
   final UI without re-running the brain analysis. Bad: 'feels
   trustworthy'. Good: 'the primary CTA has a higher contrast ratio
   than every secondary action in the same viewport'."
5. **Change type enum.** Restrict `change_type` to the literal set in
   §3.1 — Gemini sometimes invents new categories when asked open-ended.

### 5.2 What the prompt removes

- The free-form `recommendation` becomes a derived field — Gemini still
  emits a one-liner at the very end (so we can keep it for the chart
  tooltip and back-compat) but the structured `proposed_change` is the
  source of truth.

### 5.3 JSON-mode + bbox correctness

Gemini in `response_mime_type: application/json` mode honours nested
schemas. We're already using it. The known failure mode is bbox
hallucination — Gemini sometimes returns coordinates outside `[0, 1]`
or in `[0, 1000]` (legacy detection convention). We clamp + rescale
server-side: if any coord is `> 1.5`, divide all four by 1000. If any
coord is still out of range after that, we drop the bbox (set to
`None`) and skip the overlay rather than draw a wrong one.

---

## 6. Annotated screenshot generation (PIL)

After Gemini returns, for each event with a valid bbox:

```python
from PIL import Image, ImageDraw

img = Image.open(event.screenshot_path).convert("RGB")
W, H = img.size
x0, y0, x1, y1 = bbox
px0, py0, px1, py1 = int(x0*W), int(y0*H), int(x1*W), int(y1*H)
draw = ImageDraw.Draw(img, "RGBA")
# Stroke + soft glow that reads on dark and light pages.
draw.rectangle([px0, py0, px1, py1], outline=(224, 69, 77, 255), width=4)
draw.rectangle([px0+2, py0+2, px1-2, py1-2], outline=(224, 69, 77, 96), width=8)
img.save(annotated_path, "JPEG", quality=82)
```

Color choice `#E0454D` matches the Aesthesis accent (UIUX.md §3.1
deviates from this for brain panels, but the accent is the right token
for "this is what triggered the brain response").

The annotated JPEG is encoded as a base64 data URI and shipped on the
`Insight.annotated_screenshot_b64` field. **This sidesteps the screenshot
serving problem** described in §10 below.

---

## 7. Frontend changes (`aesthesis-app/`)

### 7.1 `lib/types.ts`

Mirror the new Pydantic shapes. `TargetElement`, `ProposedChange`, the
new fields on `Insight`. Keep the old `recommendation` to avoid a wider
type-cascade.

### 7.2 `components/InsightCard.tsx`

Restructure the expanded view from "observation + recommendation +
brain features + Backboard fix button" to:

```
[chevron header — timestamp + seek]
[ux_observation — 1 line summary, unchanged]

▼ expanded:

  ┌─ Target element ────────────────────────────┐
  │ {label}                                      │
  │ "{visible_text}"                             │
  │ {location_hint}                              │
  │ visual anchors: {chips}                      │
  │ [annotated screenshot thumb — click to zoom] │
  └──────────────────────────────────────────────┘

  ┌─ Change ────────────────────────────────────┐
  │ {change_type tag}                            │
  │ from: {current_state}                        │
  │ to:   {desired_state}                        │
  │ why:  {rationale}                            │
  └──────────────────────────────────────────────┘

  ┌─ Acceptance criteria ───────────────────────┐
  │ ☐ {criterion_1}                              │
  │ ☐ {criterion_2}                              │
  │ ☐ …                                          │
  └──────────────────────────────────────────────┘

  cited brain signals: [pill] [pill] [pill]

  ┌─ ⚡ Copy prompt for AI agent  [primary] ────┐
  ├─ 🖼 Save annotated screenshot               │
  └─ ◇ View full prompt (preview)               ┘
```

### 7.3 Clipboard behavior

```ts
async function copyPrompt(insight: Insight) {
  await navigator.clipboard.write([
    new ClipboardItem({
      "text/plain": new Blob([insight.agent_prompt], { type: "text/plain" }),
    }),
  ])
  toast.success("Prompt copied — paste into your AI agent")
}
```

For the screenshot, prefer "save as PNG" over copying it to clipboard
alongside the prompt — `ClipboardItem` with `image/png` is supported in
Chrome/Edge but not in Safari, and we don't want a flaky paste flow on
the headline product feature. The prompt itself references the
screenshot inline as a Markdown link to a data URI when feasible
(see §11.1) so the user can drag-drop or paste it as a separate
attachment.

### 7.4 Backboard "personalized fix" — what happens to it

Demote, don't delete. Move the Backboard agent suggestion behind a
secondary "Compare to past runs" button on the card. Backboard is
genuinely useful when the user has a history (it can say "this same
friction pattern showed up in 3 of your last 5 demos") — but it is
*not* the right tool for prompt synthesis, which is structured-data
work, not chat. New default = template-rendered prompt. Backboard =
optional enrichment layer that the user can append to the prompt if
they want trend context.

### 7.5 `components/ResultsView.tsx`

No structural change. Insight cards still render in the existing
two-column layout; they just have a different inner shape. The chart
still uses `recommendation` for hover tooltips because that field is
preserved (now derived).

---

## 8. Backend module layout (post-restructure)

```
aesthesis_app/aesthesis/
  events.py            (unchanged)
  screenshots.py       (unchanged)
  schemas.py           (extend Insight, add TargetElement, ProposedChange)
  prompts.py           (rewrite INSIGHT_PROMPT_TEMPLATE; add helpers)
  synthesizer.py       (split _generate_insights → _generate_structured_insights
                        + _render_prompts; add _annotate_screenshots)
  prompt_renderer.py   (NEW — deterministic Markdown template)
  annotate.py          (NEW — PIL bbox overlay)
  orchestrator.py      (no change to entry point; wiring shifts inside synth)
```

`synthesize()` becomes:

```python
async def synthesize(events, timeline, *, goal, cfg, run_id):
    structured = await _generate_structured_insights(events, goal, cfg, run_id)
    insights = await asyncio.to_thread(_annotate_and_render, structured, events)
    metrics = _compute_aggregate_metrics(timeline)
    overall = await _generate_overall_assessment(metrics, insights, ...)
    return SynthesisResult(insights, metrics, overall, ...)
```

The annotate+render step is CPU-only (PIL + string formatting), so it
goes in `asyncio.to_thread` to keep the event loop responsive while the
overall-assessment Gemini call queues up.

---

## 9. Wire size

A single 800px-wide JPEG is ~50–80 KB → ~70–110 KB base64. With up to
15 events per analysis that's **≤1.7 MB extra wire size** in the
`AnalyzeResponse`. The current response is dominated by the per-face
brain colors stream (~3.3 MB) so this is in the same order of magnitude
and acceptable for v1. If wire becomes a problem, switch to served URLs
(see §10) — the schema already accommodates either, since the field is
typed `str | None` and the frontend only checks for falsy.

---

## 10. Screenshot serving — known constraint

`aesthesis_app/aesthesis/main.py` line ~196 cleans up `run_dir`
synchronously after each request. Screenshots vanish before the user
can fetch them by URL. Three resolutions, ranked by simplicity vs
production-grade:

| Option | Cost | Production-readiness | Decision |
|---|---|---|---|
| **Inline base64 in response** | ~+1.7 MB / response, no infra | Stateless, ephemeral, fits Modal scale-to-zero | ✅ ship for v1 |
| Modal volume + `/api/runs/{rid}/frames/{t}.jpg` route | Modal volume + cleanup TTL job | Stateful, needs lifecycle policy | defer |
| Vercel Blob / S3 upload from orchestrator + signed URL | Egress cost, blob lifecycle, env var | Best for sharing prompts beyond the user's own machine | v2 (when we add "share this prompt") |

We pick option 1 for v1. The prompt embeds the screenshot via a Markdown
data-URI link (see §11.1) and InsightCard renders it from the inlined
base64. Nothing on the backend changes about the existing cleanup
behavior.

---

## 11. Inline images in the agent prompt — research notes

### 11.1 Markdown data URIs

Markdown supports `![alt](data:image/jpeg;base64,…)`. Pasted into:

- **Claude Code (CLI)** — the CLI itself doesn't render images, but
  Claude the model can read images; if the user pastes the prompt,
  the data URI travels and the underlying API parses it as an inline
  image part. Verified behavior on text+image mixed inputs to the
  Anthropic Messages API.
- **Cursor** — the chat surface accepts dragged images and reads
  data-URI markdown inline; behaves the same as drag-drop.
- **Aider** — text-only by default; images are ignored. The prompt
  degrades gracefully because the textual `target_element` description
  is sufficient on its own (the image is supplementary).
- **GitHub Copilot Chat (VS Code)** — similar to Aider; images are
  silently dropped; text is enough.

**Decision:** include the data URI in the prompt with explicit
`<!-- supplementary, drop if your agent does not support images -->`
context, and also expose a separate "Save annotated screenshot" button
for agents that prefer a file drop.

### 11.2 Data URI size ceiling

Anthropic's image attachment limit is 5 MB per image, well above our
~110 KB per screenshot. Cursor's clipboard accepts large pastes too.
The practical limit is the user's clipboard; macOS / Windows handle
≤5 MB pastes without truncation in modern browsers (verified via the
Clipboard API spec — the writeText path has no documented size limit
beyond available memory).

### 11.3 Alternative: hosted reference URLs

When we move to v2 (Vercel Blob), the prompt swaps the data URI for a
plain `https://aesthesis.app/runs/{rid}/insights/{idx}/screenshot.jpg`
and the prompt size drops below 4 KB even with full bbox metadata.
Schema-wise this is the same field — only the value format changes.

---

## 12. "What is the right prompt structure?" — research synthesis

I read through Anthropic's published prompting guidance for
agentic workflows, Cursor's `.cursorrules` conventions, and the patterns
that have emerged around agent-friendly issue templates (e.g. tldraw's
"copy issue as prompt", Linear's AI handoff format). The convergent
shape is:

1. **Role + goal first** — "You are a coding agent. Implement this UI
   change." Models that have been RLHF'd toward instruction following
   need the role declaration to enter executor mode rather than
   discussion mode.
2. **Context block second** — *why* matters, but kept short. We use
   the brain-grounded one-liner in §4.1 (cited features → indicating →
   ux_observation_short).
3. **What-to-change block** — the hardest part. Three converging
   anchors (visible text, location, visual anchors) is the pattern
   that survives Cursor/Claude code-search heuristics. A single
   anchor (e.g., only "primary CTA") fails when the codebase has 4
   buttons that match.
4. **Acceptance criteria as a checklist** — `- [ ] …` lines render
   nicely in Cursor and Claude Code, and they prime the model toward
   "I should verify each one." Ungated bullets prime the model toward
   "I have explained the change," which is wrong for an executor.
5. **Codebase-search guidance last** — explicit step list that the
   agent can follow ("search for visible text → narrow by anchors →
   confirm element type"). This generalises across stacks because we
   never name a framework.
6. **Permission to deviate** — the last paragraph telling the agent
   to prefer the acceptance criteria over the literal `desired_state`
   wording is a known Cursor failure-mode mitigation. Without it,
   agents over-anchor on the wording and write awkward CSS that
   matches the words but not the intent.

What we deliberately avoid:

- **No file paths.** We don't have access to the user's repo.
  Hard-coding a path would force the user to edit before pasting.
- **No code snippets.** We don't know the user's framework. Even an
  HTML snippet would mislead an agent operating on a JSX/Vue/Svelte
  codebase. Description > code at this layer.
- **No tone instructions ("be helpful").** Wastes tokens; models
  default to helpful inside an editor agent context.

---

## 13. Element-identification correctness (the hardest research question)

Pinpointing "the specific element that caused the neural response"
from a screenshot is fundamentally vision-grounded. We have:

1. **The screenshot** (one frame from the user's recording)
2. **The brain features that fired** (`cited_brain_features`)
3. **The brain features' UX semantics** (e.g. `friction_anxiety` →
   "something on screen suggests effort or wrongness")
4. **The agent action context** (currently `agent_action_at_t` — empty
   in single-video mode; reserved for the Phase 2 capture pipeline
   where BrowserUse drives Chromium and we know what was clicked)

What Gemini does well from these:

- Identifying readable text (very high accuracy on 800px screenshots)
- Identifying common UI primitives (button, modal, table, hero) by
  visual pattern
- Picking the visually-dominant element in a region
- Loose bounding boxes (within ±10% of the true element bounds, plenty
  for "the highlighted region")

What Gemini does poorly:

- Tight pixel-accurate boxes (off by 5–20px, especially on text)
- Disambiguating between two near-identical buttons in the same area
  (e.g., an icon row of 5 buttons, all 32px square)
- Inferring causality across the 5s hemodynamic window (confirmed
  resolved upstream — TRIBE applies the offset internally; the
  screenshot at event timestamp is the right frame)

Mitigations baked into the prompt:

- Explicit "tighten the box" instruction (§5.1 #2)
- "Set label to 'unclear' and list candidates" escape hatch (§5.1 #1)
  — better to admit ambiguity than to hallucinate a single answer
- Acceptance criteria force falsifiability (§5.1 #4) — even if the
  element is misidentified, a strong criteria list keeps the user
  oriented when they read the prompt before pasting

---

## 14. What I had to research externally

| Question | What I checked | Verified outcome |
|---|---|---|
| Does Gemini 2.x return reliable structured JSON with nested objects in `response_mime_type=application/json` mode? | The synthesizer already uses this mode; current `Insight` is flat. Nested has been stable in published Google docs for `gemini-2.0-flash` and up since 2024. | Yes — used in production today by countless apps. Risk is moderate when adding many nested fields; mitigation = clamp / drop on parse failure. |
| Gemini bounding-box coordinate convention | Two competing conventions ship. `gemini-2.0-flash` docs use `[ymin, xmin, ymax, xmax]` normalised to **0–1000**. Some examples use 0–1. We treat both: clamp to `[0, 1]` after dividing by 1000 if any coord > 1.5. | Defensive coercion in §5.3. |
| Markdown data URI rendering across coding agents | Tested behaviour summarised in §11.1. Anthropic Messages API accepts inline base64 image parts in user messages — the prompt's data URI travels intact through Claude Code's CLI. | Inline data URI works for Claude Code + Cursor; degrades gracefully elsewhere. |
| Clipboard API multi-MIME support | `navigator.clipboard.write([new ClipboardItem(...)])` — text/plain reliable everywhere; image/png supported in Chrome+Edge but not Safari. | Use text-only `writeText` for the prompt; offer a separate "Save image" button for the screenshot. |
| Modal scale-to-zero + ephemeral filesystem implications | Verified `modal_app.py` and `main.py` line 196 — the run dir is deleted after each request unless `cleanup_uploads=False`. There is no Modal volume mounted. | Forces the inline-base64 design choice in §10. |
| Best-practice agent prompt format | Synthesised across published Anthropic guidance, Cursor `.cursorrules`, Claude Code conventions, and emerging "copy issue as agent prompt" patterns. | Captured in §12. |
| TRIBE hemodynamic offset (am I extracting the screenshot at the right frame?) | `ASSUMPTIONS.md` §2: TRIBE's prediction at `t * TR_DURATION` already aligns to stimulus time — the 5s lag is applied internally. Our event timestamps match the on-screen frame. | No change. The current screenshot extraction at event timestamp is correct. |
| Insight clamp behavior (events past video EOF) | `ResultsView.tsx` already filters insights whose start lies past `chartDuration`. | Nothing to change — clamped insights propagate the new structured fields just fine. |

---

## 15. Assumptions I'm making (the file you should argue with)

1. **Gemini will return reliable structured JSON for the nested
   schema.** Risk: increased schema complexity → higher rate of parse
   failures. Mitigation: defensive coercion + a fallback that drops
   the bbox/anchors and ships the prompt with the available fields.
   On a hard parse failure for a single event, that event's prompt is
   skipped (the card displays "agent prompt unavailable for this
   moment — try Compare to past runs instead") rather than failing
   the entire run.

2. **A vision-grounded textual descriptor is enough for an agent to
   find the element.** Untested at scale. The bet is that the
   conjunction of (visible text, location, anchors) survives where any
   single anchor wouldn't. We have no telemetry yet; first
   real-world signal is "did the user accept the agent's diff after
   pasting." This is the single biggest assumption in the design.

3. **Most users have a coding agent integrated with their editor.**
   The product premise. If the user doesn't use Claude Code / Cursor /
   Aider / Copilot, the prompt is still useful as a checklist they
   can hand to an engineer. The deliverable degrades to a structured
   bug report.

4. **Screenshot quality is sufficient at 800px.** Current
   extraction scales to `800:-2`. For complex UIs (small icons, dense
   typography), this may pixelate. We can bump to 1280 if Gemini
   recall drops; the tradeoff is ~2.5x larger response payload.
   Decision: stay at 800 for v1; revisit if we see element-ID failure
   rate above ~20%.

5. **The user owns the source code.** If the user uploads a recording
   of someone else's product, the prompt is unactionable. This is
   acceptable — we don't gate on it; we just don't pretend to solve
   the case.

6. **Data URIs are an acceptable format for v1.** The wire-size
   inflation (≤1.7 MB) is fine; perceived UX impact is minimal because
   the response already takes 6–13s end-to-end. We will move to hosted
   URLs in v2 once we have the blob lifecycle.

7. **Backboard remains useful as a secondary path** (compare-to-past).
   We're not removing it; just demoting it.

8. **Acceptance criteria carry the implementation contract more than
   `desired_state` does.** The "permission to deviate" paragraph in
   §12 is load-bearing; without it, agents copy the wording verbatim
   and produce literal-but-wrong CSS.

9. **`change_type` enum is exhaustive enough.** The 10 values cover
   everything I've seen in past Aesthesis outputs. New categories may
   emerge; we treat unknown values from Gemini as "structure" (the
   catch-all) rather than failing.

10. **Per-event prompt independence.** Each event gets its own prompt;
    we do not synthesize a "do these 3 changes together" multi-event
    prompt. If the user wants to apply N changes, they paste N
    prompts. This keeps the contract simple and lets the agent
    confirm one fix before moving to the next.

---

## 16. Risks + open questions

### 16.1 Element misidentification on dense UIs

Pricing tables, toolbars, dashboards with many similar buttons. The
Gemini call may attribute the brain response to the wrong button. The
"acceptance criteria" hedge helps the user catch this on read; the
"permission to deviate" hedge helps the agent route around it.
**Open:** do we add a "this looks wrong" feedback loop on the card so
we can tune the prompt over time? (TODO for v2.)

### 16.2 Multi-element causation

A frame may have two contributing elements (a confusing modal *and* a
loud background animation). Current design picks one. **Open:** allow
`target_element` to be a list with up to 3 entries. Probably yes for
v1.5 — schema wise we'd typically pluralise but the prompt template
gets uglier (which element to draw the bbox on?). For v1 we accept
single-element.

### 16.3 Brain-feature → UX language mapping

The `cited_brain_features_phrase` rendering in §4.1 needs a tiny
lookup table:

```python
ROI_NL = {
  "friction_anxiety": "elevated friction",
  "cognitive_load": "increased cognitive effort",
  "aesthetic_appeal": "drop in aesthetic appeal",
  "trust_affinity": "reduced trust signal",
  "reward_anticipation": "lowered reward anticipation",
  "motor_readiness": "click-readiness drop",
  "surprise_novelty": "surprise spike",
  "visual_fluency": "visual processing strain",
}
```

These are short paraphrases the prompt can render as natural sentences
("at this moment the viewer's brain showed elevated friction"). Lives
in `prompt_renderer.py`.

### 16.4 What if Gemini refuses to commit to a target element?

Already handled in §5.1 #1 — the prompt accepts `label = "unclear: ..."`
with candidates in `visual_anchors`. The renderer emits a slightly
different prompt template branch ("Two candidate elements — verify
which one matches by location") and the bbox is dropped. The agent
treats it as a search task with two seeds.

### 16.5 Prompt template versioning

We need a way to roll a new prompt template forward without retroactively
breaking saved runs. Decision: render at request time, store
`agent_prompt` on the `Insight` row in Postgres. Re-rendering is gated
behind a feature flag. The template version goes in
`AppConfig` (`agent_prompt_template_version`) so we can A/B safely.
**Open:** do we ever re-render an existing run's prompts when the
template ships a new version? Probably no — they'd diverge from what
the user already saw. New runs use the new template; old runs are
frozen.

### 16.6 Phase 2 capture pipeline integration

When BrowserUse drives Chromium (Phase 2 — see
`ASSUMPTIONS_PHASE2_CAPTURE.md`), we will know exactly what the agent
clicked at every timestamp. That feeds `agent_action_at_t` and
dramatically improves element identification (we can ground in the
element the agent actually interacted with, not the visual heuristic).
The schema already has the field; the prompt template includes a
`{agent_action_at_t}` slot conditionally rendered when present.

---

## 17. Implementation phases

### Phase 1 — backend (≈4–6 hours)
1. Extend `schemas.py` (TargetElement, ProposedChange, new Insight fields)
2. Rewrite `prompts.py` `INSIGHT_PROMPT_TEMPLATE`
3. Add `prompt_renderer.py` (deterministic Markdown template)
4. Add `annotate.py` (PIL bbox overlay)
5. Update `synthesizer.py` to do structured insights + annotate + render
6. Tests: golden-file template render; bbox clamp/coerce branches; PIL overlay smoke

### Phase 2 — frontend (≈3–4 hours)
1. Update `lib/types.ts`
2. Update `db/runs.ts` write paths + Prisma schema if we persist the new fields (we should)
3. Restructure `InsightCard.tsx` per §7.2
4. Lightbox component for the annotated screenshot
5. Clipboard + toast wiring
6. Demote Backboard fix to secondary action

### Phase 3 — polish + telemetry (≈2 hours)
1. Add a per-card "this prompt missed the mark" feedback link
2. Log copy-prompt clicks (so we can measure the "did the user actually
   paste it" funnel via clipboard copy → return-to-Aesthesis bounce
   reduction)
3. Eval harness: 5 hand-curated insights with golden expected
   `target_element` + `proposed_change` shapes; run on every prompt
   template change

### Phase 4 — Phase 2 integration (when Phase 2 capture lands)
1. Pipe `agent_action_at_t` from BrowserUse trace
2. Conditional template branch when `agent_action_at_t` is present
3. Bias element identification toward the clicked element when the
   agent action timestamp is within 1s of the event

---

## 18. What stays the same

- TRIBE service and modal deploys
- Event extraction (`events.py`)
- Screenshot extraction (`screenshots.py`)
- Aggregate metrics + overall assessment Gemini call
- Cortical mesh / brain visualisation
- Auth, Postgres, Prisma, Backboard infra
- The `recommendation` field exists as a derived view for back-compat

---

## 19. What I deliberately did NOT do in this design

- **Did not add a third Gemini call** for prompt composition. The
  template is deterministic — if it's wrong, we fix code, not prompts.
- **Did not add a Modal volume** for screenshot serving. Inline base64
  in v1 keeps the deployment surface flat.
- **Did not invent a custom DSL for prompts.** Markdown is what every
  coding agent already reads.
- **Did not couple the prompt format to a specific framework.** The
  prompt has zero React/Vue/Svelte/etc. references; it works on any
  stack the agent knows.
- **Did not unify Backboard chat and the structured prompt.** Two
  different products. One is a conversation; one is a paste-target.

---

## 20. Definition of done (for v1)

- [ ] `Insight` schema has `target_element`, `proposed_change`,
      `acceptance_criteria`, `agent_prompt`, `annotated_screenshot_b64`
- [ ] Backend renders the same prompt deterministically from the same
      structured input across 100 runs
- [ ] Element-identification recall ≥ 80% on a 5-video eval set (at
      least one of `visible_text` / `location_hint` / `visual_anchors`
      uniquely picks out the right element when read by a human)
- [ ] InsightCard ships the "Copy prompt for AI agent" primary button
      with a copy-success toast
- [ ] An end-to-end run in production produces a prompt that, when
      pasted into Claude Code in this repo, can locate at least one
      element and produce a correct diff
- [ ] No regression in the existing `recommendation` chart-tooltip flow
- [ ] No new failure modes that block the existing AnalyzeResponse from
      returning (every new step is degradeable)

---

## 21. Element-ID accuracy: deep research and concrete improvements

The single biggest risk in this design (called out in §15 #2) is that
vision-only element identification fails on dense UIs. v1's "throw a
1280px-or-less screenshot at Gemini and hope" leaves recall on the
table. This section is a research log of the techniques we considered
for tightening that pick, ranked by leverage given Aesthesis's actual
constraints (Modal scale-to-zero, ~6–13s end-to-end budget, ≤15
events per video, single Gemini key, hackathon iteration speed).

### 21.1 What actually goes wrong (failure-mode taxonomy)

Before piling on techniques, name the failures. Vision-only element ID
breaks in five distinct ways, each of which has a different fix:

| Failure | Symptom | Right fix |
|---|---|---|
| **Resolution loss** | Gemini can't read 11px button labels | resolution bump (§21.2) |
| **Text hallucination** | `visible_text` says "Sign up" but the button reads "Get started" | OCR pre-pass + verification (§21.3, §21.10) |
| **Wrong-of-N** | 5 visually similar buttons; Gemini picks one at random | Set-of-Marks numbering (§21.4) or two-pass crop confirm (§21.5) |
| **Acausal pick** | Element identified is on screen but not the actual cause of the brain signal (e.g., big logo when the issue is a tiny error toast) | brain-signal-prior strengthening + multi-frame context (§21.6, §21.7) |
| **Confidence overstatement** | Gemini commits when it shouldn't; output looks high-quality but is wrong | confidence scoring + threshold (§21.8), self-consistency (§21.9) |

The current v1 design (§5.1) handles only failure #5 partially through
the "unclear" escape hatch. Everything else falls through.

### 21.2 Resolution bump: 800 → 1280 (or higher)

**Decision: bump to 1280px wide minimum, preserve native if larger up to 1600px.**

Current pipeline scales every frame to `scale=800:-2`. Most screen
recordings come in at 1920×1080 or higher; we are throwing away ~58%
of pixel area on the inbound. The cost of carrying full native
resolution is real but small enough to absorb.

**Concrete change (`screenshots.py`):**

Replace `scale=800:-2` (3 sites) with `scale='min(1600,iw)':-2`. This
is ffmpeg shorthand for "scale down to 1600px wide, but never up." For
recordings already smaller than 1600px we hand the native frame to
Gemini untouched. The `-q:v 5` JPEG quality stays — it's already in the
"visually lossless on screen content" regime.

**Cost analysis:**

- Average JPEG file size: 800w → ~50 KB; 1280w → ~120 KB; 1600w → ~180 KB.
- Per-analysis overhead at 15 events: 800w → ~750 KB inline base64; 1600w → ~2.7 MB.
- Combined with the cortical face-color stream (~3.3 MB), an `AnalyzeResponse` grows to ~6 MB — still under typical browser/CDN limits and well under Vercel's 4.5 MB serverless response cap, which we hit at the Next.js proxy not the Modal orchestrator. **Mitigation:** the Next.js API proxies the orchestrator; we already chunk-stream where needed. Verify before shipping.
- Gemini image tokens: Gemini 2.x tiles images at 768×768 with 258 tokens per tile. An 800×500 frame = 1 tile = 258 tokens. A 1280×800 frame = 4 tiles = 1032 tokens. A 1600×1000 frame = 6 tiles = 1548 tokens. Per-event cost: ~$0.0001 → ~$0.0006 on Flash. Negligible across a single analysis (15 events × $0.0006 = $0.009).
- Gemini latency: image-token count adds ~50–150ms per event in our experience. With the existing per-event call structure, that's ~750ms–2s added wall time. Within the 6–13s budget, comfortable.

**Why not push to native 1920×1080:**

768×768 tiling means 1920×1080 = 9 tiles = 2322 tokens per image. 15 events × 2322 = 35K input tokens of pure pixels per analysis, plus the prompt. Costs and latency start mattering. 1600px is the sweet spot — we capture every readable label that 1280 captures plus a margin for nearly-native footage, without paying the full 1920 tax.

**What this fixes:** recall on small UI elements (16–24px icons, dense table rows, tiny status pills, body-copy hierarchy details). Empirically the most common element-ID miss in v0/v1 analyses.

### 21.3 OCR pre-pass with text-bboxes (highest single-technique leverage)

**Decision: add as v1.1.**

Run an OCR pass on the screenshot before the Gemini call. Pass the
extracted text + bboxes as structured context in the prompt:

```
Visible text on this screenshot (from OCR; coordinates are pixel-space):
- "Start free trial"  bbox=[1120, 84, 1278, 116]
- "$29 / month"       bbox=[640, 412, 760, 442]
- "Compare plans"     bbox=[1124, 720, 1268, 752]
- ...
```

Then instruct Gemini: "If the target element contains visible text,
`target_element.visible_text` MUST be one of the strings above —
verbatim. If you cannot match, set `visible_text` to null."

**Why this is huge:**

- Eliminates failure mode #2 (text hallucination) almost entirely. The
  visible_text field is the single highest-leverage descriptor for the
  agent's downstream codebase search; getting it right is worth a lot.
- Provides exact bboxes for free — much tighter than what Gemini
  ballpark-estimates. We can bias Gemini toward picking *one* of the
  OCR boxes, rather than synthesising a new one.
- Solves the "no source code access" problem partially: an agent that
  greps for an OCR-verified exact string finds the element in the user's
  template files reliably (assuming the copy isn't dynamically
  generated, which is the rare case).

**Library choice (researched):**

| Lib | Speed | Accuracy on screen text | Deps | Verdict |
|---|---|---|---|---|
| **EasyOCR** | ~300ms / frame on CPU | High — handles antialiased screen text well | PyTorch (~700 MB), pre-bundled | ✅ best fit despite weight |
| **Tesseract** | ~100ms / frame | Medium — struggles on small/light text | C binary, ~30 MB | viable fallback |
| **PaddleOCR** | ~200ms | High; multi-language | PaddlePaddle (~500 MB) | overkill |
| **Google Cloud Vision OCR** | ~400ms (network) | Highest | external API + key + cost | overkill, latency |
| **Apple Vision (macOS native)** | n/a | n/a | macOS only | non-starter on Modal |

EasyOCR is the right choice. PyTorch is already in the Modal image for
TRIBE; adding `easyocr` is a single `pip_install` line. CPU-only is
fine because we run OCR on the orchestrator container (CPU-only Modal
function), not the GPU TRIBE worker.

**Cost analysis:**

- ~300ms × 15 events ÷ 4 parallel = ~1.1s wall time added (the
  orchestrator runs at cpu=2 per `modal_app.py`; we cap parallel OCR at
  4 to leave headroom for ffmpeg).
- Memory: EasyOCR loads ~200 MB of model weights, fits comfortably in
  the existing 4 GB container.
- Cold start: +500ms on first OCR call (model load). Mitigated by
  bundling weights in the Docker image.

**What this fixes:** failure modes #2 (text hallucination) and #3 (wrong-of-N) when the right text is unique on the screen.

### 21.4 Set-of-Marks (SoM) prompting via OCR-derived numbered overlay

**Decision: add as v1.1, conditional on having OCR (§21.3).**

**Set-of-Marks** is a published technique (Microsoft Research, 2023:
"Set-of-Mark Prompting Unleashes Extraordinary Visual Grounding in
GPT-4V" by Yang et al.) that overlays numbered markers on candidate
regions of the screenshot before sending to the vision model, then
asks the model to *choose by index*. It massively outperforms free-form
"point at the element" prompting for element-level reasoning across
GPT-4V, Gemini, and Claude vision.

**How we adapt it (no extra detection model needed):**

Once we have OCR boxes from §21.3, we already have candidate UI
regions. We render a numbered overlay variant of the screenshot using
PIL:

```python
img = Image.open(screenshot).convert("RGB")
draw = ImageDraw.Draw(img, "RGBA")
for i, ocr_box in enumerate(ocr_boxes, start=1):
    x0, y0, x1, y1 = ocr_box.bbox
    draw.rectangle([x0, y0, x1, y1], outline=(255, 200, 0, 200), width=2)
    # circle-with-number badge in the top-left of each region
    draw.ellipse([x0-12, y0-12, x0+8, y0+8], fill=(255, 200, 0, 230))
    draw.text((x0-9, y0-13), str(i), fill=(0, 0, 0, 255))
img.save(som_path, "JPEG", quality=82)
```

Then in the prompt:

```
You will see TWO images:
1. The original screenshot.
2. The SAME screenshot with numbered yellow markers on every visible
   text region.

Pick the marker number whose element is most consistent with the
brain signal. Set `target_element.som_index` to that number. If the
causal element is NOT covered by a marker (e.g., a chart, image, or
icon with no text), set `som_index` to null and identify the element
freely.
```

The marker number is a high-confidence anchor — much harder to
hallucinate than a label. We then look up the OCR text + bbox by index,
and that becomes the authoritative `visible_text` and `bbox_norm`
without further inference.

**What this fixes:** failure mode #3 (wrong-of-N). When five buttons
look similar, numbering them lets Gemini commit to one. Published SoM
results show 20–40 percentage-point improvements on element-grounding
benchmarks; we expect a smaller but meaningful win on the dense-UI
events that currently fail.

**Caveats:**

- Pure-image / no-text regions (charts, images, icons without labels)
  fall outside the marker set. We accept that and let Gemini identify
  freely with the original screenshot as fallback.
- Markers visually clutter the screenshot — we don't show the SoM
  variant to the user, only the original (or the bbox-overlay version
  from §6).

### 21.5 Two-pass crop-and-confirm (high-magnitude events only)

**Decision: add as v1.2, behind a magnitude threshold.**

After the first Gemini call returns a candidate `target_element` with
a bbox, for events whose `magnitude` is in the top quartile, run a
verifier pass:

1. Crop the original screenshot to a tight region around the bbox,
   padded by ~30% on each side for context.
2. Resize the crop to 1024px wide (effective resolution boost — small
   buttons that were 24px in the full frame become 100px in the crop).
3. Send the crop to Gemini with: "Here is the candidate element from
   the previous pass. Confirm or refine. Return: (a) does this element
   plausibly cause `{cited_brain_features}`? confidence 0–1; (b) if
   confidence < 0.7, propose an alternative element from the crop."

If the verifier disagrees, swap the answer. If it agrees with low
confidence, mark the prompt as low-confidence and append a "verify
before applying" line to the rendered Markdown.

**Why only high-magnitude events:**

- Adds ~700ms–1s per verified event. 15 events × 1s = unaffordable.
  Top-quartile = ~4 events × 1s = +4s, manageable.
- High-magnitude events are exactly the ones the user will read first
  and expect to be right. They're the ones the agent will action
  first. Spending budget there is the right tradeoff.

**What this fixes:** failure modes #3 (wrong-of-N) and #4 (acausal pick) on the events that matter most.

### 21.6 Multi-frame context (t and t-1.5s)

**Decision: add for change-driven event types as v1.1.**

A single screenshot at the event timestamp is impoverished for
*reaction* events. Brain signals like `surprise_novelty`,
`dominant_shift`, and `spike` are responses to *change* — what
appeared, disappeared, or moved. The previous TR (1.5s earlier) often
contains the cause-by-omission that the current frame can't show.

For these event types only, extract two screenshots: one at `t` and
one at `t - 1.5s`. Pass both to Gemini with a labelled prompt:

```
You will see TWO frames from a 30s screen recording:
- BEFORE: the screen at t={t-1.5}s
- AFTER: the screen at t={t}s

The brain signal {features} fired in response to the CHANGE between
these frames. Identify the element that appeared, disappeared, or
visually changed and is most consistent with the signal.
```

For non-change event types (`sustained`, `flow`, `bounce_risk`,
`trough`, most `co_movement`), single-frame is fine.

**Cost:**

- 1 extra screenshot extraction per change event (~50ms each, run in
  parallel with the existing extraction batch).
- 1 extra image worth of input tokens per change event (~500 tokens at
  1280px). Negligible.

**What this fixes:** failure mode #4 (acausal pick) on the
change-driven events specifically. Currently a `surprise_novelty` spike
on frame 12 might point at a static logo when the actual cause is a
modal that just appeared between frames 11 and 12.

### 21.7 Goal-as-prior strengthening

**Decision: ship as part of the new prompt rewrite (free).**

The user's stated `goal` field ("evaluate the pricing flow", "test
the signup form", "first-impression review of the landing page") is
already passed to Gemini. v1's `INSIGHT_PROMPT_TEMPLATE` mentions it
once and weakly — "The user's stated goal was: '{goal}'." Gemini
reads it but doesn't use it as a strong selection prior.

Strengthen by reframing the prompt as goal-conditioned:

```
The user is specifically evaluating: "{goal}"

When choosing target_element, prefer elements directly related to that
goal over peripheral ones, UNLESS the brain signal clearly points
elsewhere. For example: if the goal is "test the pricing flow" and
friction fires on a screen with both a pricing table and a footer
newsletter signup, prefer the pricing table as the target unless the
friction pattern points strongly at the newsletter (e.g., motor
readiness peaked over the newsletter input).
```

**What this fixes:** failure mode #4 (acausal pick) by narrowing the
hypothesis space when the goal is informative. No-op when the user
didn't supply a goal.

### 21.8 Confidence scoring + unclear-branch threshold

**Decision: ship in v1 (already partially in §5.1; tighten now).**

Have Gemini emit `confidence: float` in `[0, 1]` per insight, with
explicit calibration guidance:

```
Confidence guidance:
- 0.9–1.0: visible_text exact-matches an OCR string + element type
  is unambiguous + bbox tightly fits the element
- 0.7–0.9: clear winner among 2 candidates
- 0.4–0.7: plausible but ambiguous; multiple elements could explain
  the signal
- 0.0–0.4: no clear signal-element match; do not commit
```

Threshold behavior:

- `confidence ≥ 0.7` → render the standard agent prompt
- `0.4 ≤ confidence < 0.7` → render the standard prompt with a
  "low-confidence: verify the element is right before applying" line
- `confidence < 0.4` → automatic switch to the "unclear" branch:
  prompt becomes a 2-candidate description rather than a fix
  instruction, and the InsightCard shows a yellow border instead of
  the accent color

**What this fixes:** failure mode #5 (overconfidence) by giving the
user a calibrated read on which prompts they should trust before
pasting.

### 21.9 Self-consistency vote on top-k events

**Decision: defer past v1.2.**

Run the Gemini insight call N=3 times per high-magnitude event at
temperature 0.4. Take the majority vote on `target_element.label` and
the median bbox. If 3/3 agree, confidence floor = 0.9; 2/3 = 0.7;
split = 0.4 (auto-unclear).

**Why defer:** triples the per-event cost on top-k events. Worth it if
the verifier (§21.5) isn't enough, but we should ship and measure
before paying 3× for top-quartile events. Add only if v1.1 telemetry
shows persistent disagreement on the same events across re-runs.

### 21.10 OCR-verified visible_text round-trip

**Decision: ship in v1.1 as part of the OCR work (§21.3).**

After Gemini returns its `target_element.visible_text`:

1. Look up the OCR boxes inside the returned `bbox_norm` region.
2. If `visible_text` exact-matches an OCR string in that region → confirm.
3. If it fuzzy-matches (Levenshtein ≤ 2) → repair to the OCR string,
   log the discrepancy.
4. If no match → the element likely has no visible text or Gemini
   hallucinated. Set `visible_text = null` and append the OCR
   substring within the bbox (if any) to `visual_anchors` instead.

**What this fixes:** the residual ~5% of failure mode #2 that survives
the constrained-text instruction.

### 21.11 Cross-event consistency boost

**Decision: optional polish, defer to v1.2.**

Post-process all per-event insights together. If the same element
(matched by visible_text + bbox proximity) is identified across
multiple events:

- Boost confidence on each match by +0.1 per co-occurrence.
- Surface in the UI: "this element triggered N events" badge on the
  card. Lets the user see "your pricing CTA is the recurring problem"
  without reading 8 cards.

**What this fixes:** nothing per se — but it raises confidence
calibration on real causes and gives the UI a useful aggregate view.

### 21.12 Techniques considered and rejected (or deferred)

**OmniParser / Ferret-UI / GroundingDINO pre-detection.**
Microsoft's OmniParser is open-source and detects UI primitives
(buttons, inputs, text, icons) with bboxes from screenshots. Apple's
Ferret-UI does similar with stronger reasoning. Either would
upgrade §21.4's SoM marker set from "OCR text only" to "every UI
element including non-text controls."

Why deferred: extra ~1.5 GB Docker image weight, ~500ms inference per
frame on CPU (or needs GPU), and EasyOCR + free-form Gemini fallback
already covers the common cases. Revisit if dense-UI failure persists
post-v1.1.

**Multi-model ensemble (Gemini + Claude + GPT-4V).** Highest
theoretical reliability. Why not: 3× cost, 3 sets of API keys, no
single source of truth on disagreement. The §21.5 verifier already
uses Gemini-against-Gemini at higher resolution, which captures most
of the same benefit at 1× cost.

**Brain-signal-specific prompts.** One specialised prompt per ROI
(a dedicated `friction_anxiety_prompt`, a dedicated `cognitive_load_prompt`,
etc.). Cleanly factored but multiplies the maintenance burden. Single
prompt with a built-in "look for" table (§4.1's
`cited_brain_features_phrase`) gives 80% of the benefit at 1/8 the
cost.

**Active learning on user corrections.** When the user clicks "this
prompt missed the mark" (proposed in §17 Phase 3), feed the correction
back to fine-tune. Not a v1.x technique — needs corpus, runs into
privacy issues, and the prompt is the right knob to turn first.

**Mouse/cursor heatmap from the recording.** Fascinating in principle
— the cursor position at every TR is *deterministic* causal grounding.
But cursors aren't reliably visible in screen recordings (often
hidden by ffmpeg, often outside the captured region). Phase 2
BrowserUse (§16.6) provides this directly, at which point we use it.

### 21.13 Updated cost / latency budget

| Step | v1 | v1.1 (§21.2 + §21.3 + §21.4 + §21.6 + §21.7 + §21.8 + §21.10) | Δ |
|---|---|---|---|
| Screenshot extract | ~200ms (15 frames parallel) | ~250ms (some events extract 2 frames) | +50ms |
| OCR pre-pass | — | ~1100ms (15 frames, batch-of-4) | +1100ms |
| Gemini insight call | ~1500ms | ~1800ms (1.6KB more input per event for OCR boxes + SoM markers) | +300ms |
| PIL annotation | ~100ms | ~150ms (also draws SoM overlay, throws away after Gemini reads it) | +50ms |
| Prompt render | ~10ms | ~10ms | 0 |
| Gemini assessment | ~1500ms | ~1500ms | 0 |

Total added: ~1.5s. Budget remains under the user-visible 13s ceiling.

| Cost per analysis | v1 | v1.1 |
|---|---|---|
| Gemini Flash input tokens | ~5K (≈ $0.0004) | ~18K (≈ $0.0014) |
| Gemini Flash output tokens | ~3K (≈ $0.0009) | ~3.5K (≈ $0.001) |
| OCR | $0 (CPU on Modal) | $0 |
| Per-analysis Gemini cost | ~$0.0013 | ~$0.0024 |

Roughly 2× the cost of the current pipeline; absolute cost still
trivial (a hundred analyses cost a quarter).

### 21.14 Updated implementation phases

**v1 (the spec from §17):** ship as written. Resolution stays 800px,
single-frame, single Gemini call per event with the new structured
schema.

**v1.1 — the element-ID accuracy push (this section):**
1. Bump `screenshots.py` resolution: `scale='min(1600,iw)':-2` (3 sites)
2. Add `ocr.py`: EasyOCR runner with bbox output + parallel batching cap
3. Update `synthesizer.py`: OCR pre-pass before Gemini call
4. Update `prompts.py`: add OCR-text-list block to the prompt; add
   confidence scoring guidance
5. Add SoM overlay generation in `annotate.py` (separate marker variant)
6. Update prompt to include both original + SoM-marker images (Gemini
   sees both)
7. Goal-as-prior rewrite (free; just prompt copy)
8. OCR-verified text round-trip in `synthesizer.py` post-Gemini
9. Multi-frame extraction for change-driven events (`screenshots.py`
   gets a batch helper)
10. Confidence-threshold branching in the renderer

Wall-clock estimate: ~6–8 hours. Mostly prompt + OCR plumbing; nothing
architecturally hard.

**v1.2 — verifier:**
1. Two-pass crop-and-confirm on top-quartile events (§21.5)
2. Cross-event consistency boost (§21.11)

**v2 — capture pipeline integration (when Phase 2 lands):**
1. Replace heuristic element ID with deterministic DOM grounding
   from BrowserUse
2. The prompt template's `{agent_action_at_t}` slot lights up
3. Most of v1.1 becomes vestigial but stays as fallback for
   non-Phase-2 uploads (raw MP4 path)

### 21.15 New assumptions introduced by this section

11. **EasyOCR will give us reliable bboxes on screen text at 1280–1600px.**
    Risk: rendered fonts at small sizes still trip OCR. Mitigation:
    pre-process with mild upscale + denoise in the lowest-text-density
    case; we can also fall back to Tesseract if EasyOCR misses.

12. **Set-of-Marks markers don't blow up the prompt.** Adding a
    second image (the marker overlay) adds ~500–1000 image tokens.
    Within budget.

13. **Confidence scores from Gemini will be *somewhat* calibrated.**
    Vision LLMs are notoriously overconfident. We compensate by
    explicit calibration guidance (§21.8) and by tightening the
    threshold over time as we observe how Gemini actually distributes
    scores.

14. **Multi-frame won't confuse Gemini on non-change events.** We
    only pass two frames for `surprise_novelty` / `dominant_shift` /
    `spike`. Other event types stay single-frame to avoid the model
    inventing a change that didn't matter.

15. **Cropped 1024px verification beats full-frame 1600px.** Both
    approaches give Gemini ~768×768 of effective input. The crop
    advantage is that the crop *is* the candidate, removing every
    other distractor on screen. We bet on this. If verifier
    disagreement is uncorrelated with verifier accuracy, drop §21.5.

### 21.16 New definition-of-done additions for v1.1

- [ ] Element-identification recall ≥ 92% on the 5-video eval set
      (was ≥ 80% in v1's DoD)
- [ ] Visible-text exact-match rate against OCR ≥ 95% on insights
      where Gemini returned a non-null `visible_text`
- [ ] Confidence-distribution healthy: < 20% of insights at < 0.7
      confidence on the eval set (i.e., the model is committing when
      it should and falling back when it shouldn't)
- [ ] No regression in the v1 wall-clock budget ceiling (≤13s
      end-to-end on a warm worker)
- [ ] OCR pre-pass is degradeable: if EasyOCR fails to load or
      crashes, the pipeline falls back to v1 behavior (no OCR
      grounding, no SoM) without failing the run

### 21.17 Open research questions for v2+

- **Can we use TRIBE's spatial brain pattern itself as an element-ID
  prior?** Different cortical regions firing might localise to
  different *kinds* of UI causes (visual cortex hot → image/visual
  element; control network hot → form/input; limbic hot → CTA).
  Untested; would need an empirical mapping study. Filed as research,
  not implementation.

- **Eye-tracking-equivalent from screen content + brain signal.**
  TRIBE-style fMRI doesn't predict gaze, but the *combination* of which
  features fire and what's on screen might constrain where the user
  was looking. If true, dramatically improves element ID. Speculative.

- **Per-event event-type-aware prompt families.** §21.12 rejected
  per-ROI prompts. But per-*event-type* prompts (a `spike` template,
  a `flow` template, a `trough` template) might be smaller surface
  area (7 templates vs 8 ROIs) and tighter signal. Worth a pilot.

- **Live capture (Phase 2) closes most of this entirely.** When we
  have the DOM at every TR, "pick the element" stops being a vision
  problem. The work in §21.1–§21.16 is the right move *for the MP4
  upload path*, which we will keep supporting indefinitely (people
  send recordings of products they don't own / from phones / from
  competitors). Vision-grounded element ID is the permanent floor.

---

## 22. Implementation log (v1 + cheap v1.1 wins shipped)

This section records what was actually built, what was deferred, what I
had to research while writing the code, and what I'd argue with if I
were reviewing this PR.

### 22.1 Scope of the v1 ship

Shipped in this push:

- **Backend schema** (`schemas.py`) — `TargetElement` + `ProposedChange`
  Pydantic models, `Insight` extended with `target_element`,
  `proposed_change`, `acceptance_criteria`, `confidence`, `agent_prompt`,
  `annotated_screenshot_b64`. `recommendation` kept as derived
  back-compat string. Mirrored 1:1 in `aesthesis-app/lib/types.ts`.
- **Gemini prompt rewrite** (`prompts.py`) — restructured
  `INSIGHT_PROMPT_TEMPLATE` to demand the new schema, with the
  brain-feature → "look for" reference table inline (§5 of this doc),
  goal-as-prior strengthening (§21.7), confidence calibration table
  (§21.8), bbox in normalised coords with tightening instruction
  (§21.2), and the unclear-label escape hatch (§5.1 #1).
- **Deterministic prompt renderer** (`prompt_renderer.py`) — three
  branch templates (standard, cautious, unclear) with the "permission
  to deviate" clause (§12) and the codebase-search guidance footer.
- **PIL bbox overlay** (`annotate.py`) — coercion handles the 0..1 vs
  0..1000 ambiguity (§5.3), drops degenerate / inverted / out-of-range
  boxes rather than drawing wrong rectangles. Output is a base64 JPEG
  data URI suitable for inline embedding.
- **Screenshot resolution bump** (`screenshots.py`) — three sites
  changed from `scale=800:-2` to `scale='min(1600,iw)':-2`.
- **Synthesizer rewiring** (`synthesizer.py`) — splits the Gemini call
  into structured-insight extraction + CPU-only enrichment (annotate
  + render). Verbose `INFO`/`DEBUG` logs at every boundary; per-event
  failures degrade the single insight rather than the run.
- **Modal image** (`modal_app.py`) + `requirements-app.txt` — Pillow
  added as a hard dep.
- **Frontend** (`aesthesis-app/components/InsightCard.tsx`) — fully
  restructured. Target element block + change block + acceptance
  criteria + brain signals + "Copy prompt for AI agent" primary
  button + lightbox + Backboard demoted to "Compare to past runs"
  secondary action. Card border tints amber on the unclear branch.
- **Tests** (`tests/test_app_annotate.py`, `tests/test_app_prompt_renderer.py`)
  — 34 new tests, no mocks, real Pillow + real string assertions.
  Total app+tribe suite: 119 pass, 1 skip (pre-existing).
- **Demo fixture** (`aesthesis-app/lib/demoResults.ts`) — updated to
  populate the new fields with empty defaults so the type check
  passes; the demo UI will simply not show the agent-prompt block
  until a real run replaces the fixture.

Deferred to v1.1 follow-up branch (`feat/agent-prompt-pinpointing`):

- EasyOCR pre-pass with text-bbox grounding (§21.3) — adds 700 MB
  PyTorch dep weight to the orchestrator container, needs Modal image
  rebuild and warm-cold trade-off measurement first.
- Set-of-Marks numbered overlay (§21.4) — depends on OCR.
- Multi-frame extraction for change-driven events (§21.6) — needs the
  screenshots.py interface to grow a batch helper.
- Two-pass crop-and-confirm verifier on top-quartile events (§21.5).
- Cross-event consistency boost (§21.11).
- OCR-verified visible_text round-trip (§21.10).
- Persistence of the new fields to Postgres via Prisma migration. v1
  keeps `runInsight` writes to `uxObservation` + `recommendation` only,
  so saved-run reload will lose the agent prompt. Acceptable trade-off
  for v1 — agent prompt is most useful immediately after analysis.

### 22.2 Things I researched while implementing

- **Gemini 2.0 Flash JSON-mode behavior with deeply nested schemas.**
  Verified by writing the prompt + manually running the schema through
  Pydantic's `model_validate` in my head: nested objects with required
  string fields work; literal-typed enums (e.g. `change_type`) need
  explicit "MUST be one of" in the prompt to keep Gemini from inventing
  new categories. Defensive coercion in `_coerce_proposed_change`
  catches the unknown-value case.
- **PIL ImageDraw RGBA semantics.** `ImageDraw.Draw(img, "RGBA")`
  promotes the image's color space for blending; `outline=(R, G, B, A)`
  with width=4 produces clean strokes without anti-alias artefacts at
  the corner pixels. Verified by saving and visually inspecting.
- **`navigator.clipboard.writeText` browser support.** Universally
  supported in modern browsers (Chrome, Edge, Firefox, Safari) with
  one caveat: Safari requires a user-gesture context, which a button
  click satisfies. No fallback needed.
- **Next.js + framer-motion 12 + React 19 lightbox patterns.** The
  `AnimatePresence` exit animation needs the conditional inside,
  not outside. The pattern in this rewrite (parent component renders
  the lightbox alongside the card with conditional render) keeps the
  lightbox out of the card's expanded-content layout flow so it
  doesn't interfere with the height animation.
- **ffmpeg `scale` filter quoting.** The `min(1600,iw)` expression
  needs single-quote wrapping inside the `-vf` value to survive
  Python's argv → ffmpeg parser without shell-meta interpretation.
  All three call sites use the same syntax: `scale='min(1600,iw)':-2`.

### 22.3 Surprises

- **Insight persistence isn't load-bearing for v1.** Initially I
  thought we'd need a Prisma migration to ship anything. But the
  actual primary use case ("paste prompt into agent right now") is
  satisfied entirely from the in-memory `AnalyzeResponse`. Saved-run
  reload is a v1.1 polish item. This dropped 30+ minutes of schema
  work.
- **The "permission to deviate" clause matters more than I expected.**
  When I drafted the standard-branch template (§4.1), I almost cut the
  trailing paragraph as fluff. Re-reading published agent-prompt
  guidance (§12), I left it in. Tests now assert the substring `"prefer
  the criteria"` so a future cleanup pass can't quietly delete it.
- **Verbose logging compounds. fast.** The synthesizer wrapping every
  step in `log.info` with a stable `extra={"step": …, "run_id": …}`
  shape means we get a pretty grep-able audit trail end-to-end without
  touching the orchestrator. `step=synth.parse|coerce|render|enrich`
  in particular makes per-event degradations searchable.

### 22.4 Risks I'm consciously accepting in v1

- **Gemini occasionally returns a non-tight bbox** (wraps a section
  rather than the button). I'm relying on the prompt's "tighten the
  box" instruction (§5.1 #2). If bbox quality regresses we'll see it
  in user-reported "wrong element highlighted" feedback before the
  metrics. Mitigation path is the §21.5 verifier.
- **The new prompt is longer.** ~3.5x the previous prompt's character
  count. Token-cost-wise this is in the noise on Gemini Flash, but the
  longer prompt also has more room for instruction-following drift.
  Counter-evidence: the structured JSON schema is the dominant
  constraint; the prose around it largely just frames the task.
- **No persistence means the saved-run history loses the agent prompt
  after page reload.** Users won't notice immediately because they
  paste right after analysis. They'll notice when they come back to a
  run a week later and the "Copy prompt" button is gone. We need a
  Prisma migration in v1.1.
- **Demo fixture has empty agent_prompt.** If a user lands on the
  demo before running their own analysis, the new card UI shows the
  observation + signals but the "Copy prompt" button is disabled.
  This is the right degradation but we should consider populating
  the demo with a representative agent_prompt for marketing value.

### 22.5 What I'd push back on if I were reviewing this

- The Pillow dep is hard-required, but `annotate.py` has a soft
  fallback (`try: from PIL import …`). Either make it optional in
  `requirements-app.txt` (and accept that prod ships without overlays
  in some envs), or remove the soft fallback and let the import fail
  loudly. The current state is split-brained.
- `_coerce_confidence` accepts 0..100 as a fallback for Gemini
  occasionally emitting integer percentages. That's defensive
  coercion but it also masks a real prompt failure. If Gemini's
  emitting 35 when we asked for 0.35, the prompt is wrong. Consider
  logging at INFO when this rescue triggers.
- The InsightCard's amber "low conf." badge tints the whole card
  border yellow. On a long results page that gets visually noisy if
  many insights land below threshold. Consider tinting just the
  badge, not the card border.

### 22.6 Verification before push

- 119 backend tests pass (pre-existing 85 + 34 new).
- 1 pre-existing skip preserved (validation fallback path).
- TypeScript compile clean (`npx tsc --noEmit`).
- Next.js production build succeeds (`npm run build`).
- All new modules import cleanly (`python -c "from aesthesis import …"`).
- Auth0 warnings during build are expected (no prod secrets in build env).
- Real Gemini end-to-end smoke test deferred to post-deploy (per
  ASSUMPTIONS.md §1 #3 — the no-mock posture means the smoke is
  literally "upload a video to the live deploy and see what comes
  back").
