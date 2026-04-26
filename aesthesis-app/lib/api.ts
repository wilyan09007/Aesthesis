// Aesthesis backend client.
//
// One function: analyze(). Multipart POST to /api/analyze with the MP4 and
// an optional goal. Returns the parsed AnalyzeResponse.
//
// Verbose console logging is intentional. The full pipeline takes ~6-13s
// (TRIBE GPU + two Gemini calls) and a silent network tab is hostile to
// debugging. Every log line includes the run_id when we have it so you
// can grep across browser and server logs.
//
// Single-video pivot (DESIGN.md §17): the request body now sends one
// `video` field instead of `video_a` + `video_b`. The legacy two-video
// signature is gone.

import type { AnalyzeResponse, ValidationFailure } from "./types"

export const API_BASE_URL: string =
  // NEXT_PUBLIC_ vars are inlined at build time, so this is fine in the
  // browser. Default targets `uvicorn aesthesis.main:app --port 8000` on
  // localhost — the dev setup documented in the backend README.
  process.env.NEXT_PUBLIC_AESTHESIS_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000"

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
