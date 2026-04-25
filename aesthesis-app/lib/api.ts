// Aesthesis backend client.
//
// Skip path: analyze() — multipart POST to /api/analyze with the MP4 and an
// optional goal. Returns the parsed AnalyzeResponse.
//
// Phase 2 capture path:
//   - startCaptureRun(url, goal, auth?) -> {run_id} via POST /api/run
//   - LiveStreamPanel opens wsUrl(`/api/stream/{run_id}`) for live frames
//   - on capture_complete -> fetchCapturedVideo(run_id) -> File
//   - then analyzeByRunId(run_id, goal) -> AnalyzeResponse
//
// Verbose console logging is intentional. The full pipeline takes ~6-13s
// for analysis (TRIBE GPU + two Gemini calls) plus ~30s of capture, and
// a silent network tab is hostile to debugging. Every log line includes
// the run_id when we have it so you can grep across browser and server logs.
//
// Single-video pivot (DESIGN.md §17): the request body sends one `video`
// field — legacy two-video signature is gone.

import type {
  AnalyzeResponse, CachedDemoEntry,
  RunStartedResponse, ValidationFailure,
} from "./types"

export const API_BASE_URL: string =
  // NEXT_PUBLIC_ vars are inlined at build time, so this is fine in the
  // browser. Default targets `uvicorn aesthesis.main:app --port 8000` on
  // localhost — the dev setup documented in the backend README.
  process.env.NEXT_PUBLIC_AESTHESIS_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000"

// D18 — derived from API_BASE_URL by swapping http(s)→ws(s). The previous
// LiveStreamPanel used a relative WS URL which silently hit the Next.js
// dev server on :3000 instead of FastAPI on :8000. Bug fixed at the
// helper layer so every WS path benefits.
export const WS_BASE_URL: string = API_BASE_URL
  .replace(/^http:\/\//, "ws://")
  .replace(/^https:\/\//, "wss://")

export function wsUrl(path: string): string {
  // Path may or may not start with /. Normalize.
  const p = path.startsWith("/") ? path : `/${path}`
  return `${WS_BASE_URL}${p}`
}

export class AnalyzeError extends Error {
  status: number
  body: unknown
  runId: string | null

  constructor(message: string, status: number, body: unknown, runId: string | null) {
    super(message)
    this.name = "AnalyzeError"
    this.status = status
    this.body = body
    this.runId = runId
  }
}

export type AnalyzeArgs = {
  file: File
  goal?: string | null
  signal?: AbortSignal
}

function logScope(rid: string | null) {
  const tag = rid ? `[aesthesis:${rid.slice(0, 8)}]` : "[aesthesis]"
  return {
    info: (msg: string, extra?: unknown) =>
      // eslint-disable-next-line no-console
      console.info(`${tag} ${msg}`, extra ?? ""),
    warn: (msg: string, extra?: unknown) =>
      // eslint-disable-next-line no-console
      console.warn(`${tag} ${msg}`, extra ?? ""),
    error: (msg: string, extra?: unknown) =>
      // eslint-disable-next-line no-console
      console.error(`${tag} ${msg}`, extra ?? ""),
  }
}

export async function analyze({
  file,
  goal,
  signal,
}: AnalyzeArgs): Promise<AnalyzeResponse> {
  const url = `${API_BASE_URL}/api/analyze`
  const fd = new FormData()
  fd.append("video", file, file.name || "video.mp4")
  if (goal && goal.trim()) fd.append("goal", goal.trim())

  const log = logScope(null)
  log.info("analyze.begin", {
    url,
    file: { name: file.name, size: file.size, type: file.type },
    goal_present: !!(goal && goal.trim()),
  })

  const t0 = performance.now()
  let resp: Response
  try {
    resp = await fetch(url, {
      method: "POST",
      body: fd,
      signal,
      // Browser sets multipart Content-Type with the right boundary.
      // Don't set it manually — that breaks the boundary.
      cache: "no-store",
    })
  } catch (e) {
    const elapsed_ms = Math.round(performance.now() - t0)
    log.error("analyze.network_failure", { elapsed_ms, error: String(e) })
    throw new AnalyzeError(
      `Network error reaching ${url}: ${String(e)}. ` +
        `Is the backend running? (uvicorn aesthesis.main:app --port 8000)`,
      0,
      null,
      null,
    )
  }

  const rid = resp.headers.get("X-Aesthesis-Run-Id")
  const serverElapsed = resp.headers.get("X-Aesthesis-Elapsed-Ms")
  const elapsed_ms = Math.round(performance.now() - t0)
  const scoped = logScope(rid)

  if (!resp.ok) {
    let body: unknown = null
    try {
      body = await resp.json()
    } catch {
      try {
        body = await resp.text()
      } catch {
        /* swallow */
      }
    }
    scoped.error("analyze.http_error", {
      status: resp.status,
      elapsed_ms,
      server_elapsed_ms: serverElapsed,
      body,
    })

    // FastAPI maps OrchestratorError → 400 with ValidationFailure body
    // wrapped in `detail`; bubble up a useful message.
    let message = `Backend returned ${resp.status}`
    if (body && typeof body === "object" && "detail" in (body as object)) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === "string") {
        message = detail
      } else if (detail && typeof detail === "object") {
        const v = detail as ValidationFailure
        if (v.field && v.error) message = `${v.field}: ${v.error}`
      }
    }
    throw new AnalyzeError(message, resp.status, body, rid)
  }

  let json: AnalyzeResponse
  try {
    json = (await resp.json()) as AnalyzeResponse
  } catch (e) {
    scoped.error("analyze.parse_failure", { elapsed_ms, error: String(e) })
    throw new AnalyzeError(
      `Backend returned 200 but the body wasn't valid JSON: ${String(e)}`,
      resp.status,
      null,
      rid,
    )
  }

  scoped.info("analyze.done", {
    elapsed_ms,
    server_elapsed_ms: serverElapsed,
    n_insights: json.insights?.length ?? 0,
    n_events: json.events?.length ?? 0,
    n_metrics: json.aggregate_metrics?.length ?? 0,
  })

  return json
}

// Lightweight liveness probe — surfaces backend reachability without
// actually starting an analysis. Used for the dev banner and could
// be reused in a /status route handler later.
export async function pingHealth(signal?: AbortSignal): Promise<{
  ok: boolean
  detail: unknown
  elapsed_ms: number
}> {
  const url = `${API_BASE_URL}/health`
  const t0 = performance.now()
  try {
    const r = await fetch(url, { signal, cache: "no-store" })
    const elapsed_ms = Math.round(performance.now() - t0)
    const detail = await r.json().catch(() => null)
    return { ok: r.ok, detail, elapsed_ms }
  } catch (e) {
    return {
      ok: false,
      detail: { error: String(e) },
      elapsed_ms: Math.round(performance.now() - t0),
    }
  }
}

// ── Phase 2: capture endpoints ──────────────────────────────────────────

export type StartCaptureArgs = {
  url: string
  goal?: string | null
  auth?: { cookies?: Array<Record<string, unknown>> } | null
  signal?: AbortSignal
}

/**
 * D11 step 1: kick off a capture run.
 *
 * On success returns the backend-assigned run_id. The frontend then opens
 * a WebSocket to wsUrl(`/api/stream/${run_id}`).
 *
 * Throws AnalyzeError on:
 *   - 409 with `error: "capture_in_progress"` when D19 cap is hit
 *   - any other non-2xx
 *   - network failures
 */
export async function startCaptureRun(args: StartCaptureArgs): Promise<RunStartedResponse> {
  const url = `${API_BASE_URL}/api/run`
  const log = logScope(null)
  log.info("startCaptureRun.begin", {
    url, target_url: args.url,
    goal_present: !!(args.goal && args.goal.trim()),
    n_cookies: args.auth?.cookies?.length ?? 0,
  })

  const body: Record<string, unknown> = { url: args.url }
  if (args.goal && args.goal.trim()) body.goal = args.goal.trim()
  if (args.auth) body.auth = args.auth

  const t0 = performance.now()
  let resp: Response
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: args.signal,
      cache: "no-store",
    })
  } catch (e) {
    const elapsed_ms = Math.round(performance.now() - t0)
    log.error("startCaptureRun.network_failure", { elapsed_ms, error: String(e) })
    throw new AnalyzeError(
      `Network error reaching ${url}: ${String(e)}. ` +
        `Is the backend running? (uvicorn aesthesis.main:app --port 8000)`,
      0, null, null,
    )
  }

  const elapsed_ms = Math.round(performance.now() - t0)

  if (!resp.ok) {
    let body: unknown = null
    try { body = await resp.json() } catch { /* swallow */ }
    log.error("startCaptureRun.http_error", { status: resp.status, elapsed_ms, body })

    let message = `Backend returned ${resp.status}`
    let runId: string | null = null
    if (body && typeof body === "object" && "detail" in (body as object)) {
      const detail = (body as { detail: unknown }).detail
      if (detail && typeof detail === "object") {
        const d = detail as Record<string, unknown>
        if (typeof d.message === "string") message = d.message
        if (typeof d.active_run_id === "string") runId = d.active_run_id
      }
    }
    throw new AnalyzeError(message, resp.status, body, runId)
  }

  const json = (await resp.json()) as RunStartedResponse
  const scoped = logScope(json.run_id)
  scoped.info("startCaptureRun.done", { elapsed_ms, run_id: json.run_id })
  return json
}

/**
 * D11 step 3: analyze a captured MP4 by run_id reference (no re-upload).
 *
 * The MP4 lives server-side at upload_dir/{run_id}/video.mp4 from the
 * earlier capture. Backend orchestrator picks it up + threads any
 * actions.jsonl in the same dir for D15 action stamping.
 *
 * D33 cleanup: success deletes the run dir; failure retains for debug.
 */
export type AnalyzeByRunArgs = {
  runId: string
  goal?: string | null
  signal?: AbortSignal
}

export async function analyzeByRunId(args: AnalyzeByRunArgs): Promise<AnalyzeResponse> {
  const url = `${API_BASE_URL}/api/analyze/by-run/${encodeURIComponent(args.runId)}`
  const log = logScope(args.runId)
  log.info("analyzeByRunId.begin", { url, goal_present: !!(args.goal && args.goal.trim()) })

  const body: Record<string, unknown> = {}
  if (args.goal && args.goal.trim()) body.goal = args.goal.trim()

  const t0 = performance.now()
  let resp: Response
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: args.signal,
      cache: "no-store",
    })
  } catch (e) {
    const elapsed_ms = Math.round(performance.now() - t0)
    log.error("analyzeByRunId.network_failure", { elapsed_ms, error: String(e) })
    throw new AnalyzeError(
      `Network error reaching ${url}: ${String(e)}.`,
      0, null, args.runId,
    )
  }

  const elapsed_ms = Math.round(performance.now() - t0)
  if (!resp.ok) {
    let body: unknown = null
    try { body = await resp.json() } catch { /* swallow */ }
    log.error("analyzeByRunId.http_error", { status: resp.status, elapsed_ms, body })

    let message = `Backend returned ${resp.status}`
    if (body && typeof body === "object" && "detail" in (body as object)) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === "string") message = detail
      else if (detail && typeof detail === "object") {
        const v = detail as ValidationFailure
        if (v.field && v.error) message = `${v.field}: ${v.error}`
        const d = detail as Record<string, unknown>
        if (typeof d.message === "string") message = d.message
      }
    }
    throw new AnalyzeError(message, resp.status, body, args.runId)
  }

  const json = (await resp.json()) as AnalyzeResponse
  log.info("analyzeByRunId.done", {
    elapsed_ms,
    n_insights: json.insights?.length ?? 0,
    n_events: json.events?.length ?? 0,
  })
  return json
}

/**
 * D11 step 2: download the captured MP4 as a Blob. Used by AnalyzingView
 * to show a preview thumbnail during the 3s confirm countdown.
 */
export async function fetchCapturedVideo(runId: string, signal?: AbortSignal): Promise<Blob> {
  const url = `${API_BASE_URL}/api/run/${encodeURIComponent(runId)}/video`
  const log = logScope(runId)
  log.info("fetchCapturedVideo.begin", { url })
  const t0 = performance.now()

  const resp = await fetch(url, { signal, cache: "no-store" })
  const elapsed_ms = Math.round(performance.now() - t0)
  if (!resp.ok) {
    log.error("fetchCapturedVideo.http_error", { status: resp.status, elapsed_ms })
    throw new AnalyzeError(
      `Captured video not available (status ${resp.status})`,
      resp.status, null, runId,
    )
  }
  const blob = await resp.blob()
  log.info("fetchCapturedVideo.done", { elapsed_ms, size_bytes: blob.size })
  return blob
}

/**
 * D29: list cached demos for the stage-day fallback button. Returns []
 * if backend has no manifest configured (the optional infrastructure).
 */
export async function fetchCachedDemos(signal?: AbortSignal): Promise<CachedDemoEntry[]> {
  const url = `${API_BASE_URL}/api/cached-demos`
  try {
    const resp = await fetch(url, { signal, cache: "no-store" })
    if (!resp.ok) return []
    return (await resp.json()) as CachedDemoEntry[]
  } catch {
    return []
  }
}
