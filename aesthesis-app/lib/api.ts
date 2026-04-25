// Aesthesis backend client.
//
// One function: analyze(). Multipart POST to /api/analyze with the two
// MP4s and an optional goal. Returns the parsed AnalyzeResponse.
//
// Verbose console logging is intentional. The full pipeline takes 12-25s
// (TRIBE GPU + two Gemini calls) and a silent network tab is hostile to
// debugging. Every log line includes the run_id when we have it so you
// can grep across browser and server logs.

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
  fileA: File
  fileB: File
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
  fileA,
  fileB,
  goal,
  signal,
}: AnalyzeArgs): Promise<AnalyzeResponse> {
  const url = `${API_BASE_URL}/api/analyze`
  const fd = new FormData()
  fd.append("video_a", fileA, fileA.name || "a.mp4")
  fd.append("video_b", fileB, fileB.name || "b.mp4")
  if (goal && goal.trim()) fd.append("goal", goal.trim())

  const log = logScope(null)
  log.info("analyze.begin", {
    url,
    file_a: { name: fileA.name, size: fileA.size, type: fileA.type },
    file_b: { name: fileB.name, size: fileB.size, type: fileB.type },
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
    n_insights_a: json.a?.insights?.length ?? 0,
    n_insights_b: json.b?.insights?.length ?? 0,
    n_events_a: json.a?.events?.length ?? 0,
    n_events_b: json.b?.events?.length ?? 0,
    winner: json.verdict?.winner,
  })

  return json
}

// Lightweight liveness probe — surfaces backend reachability without
// actually starting a 25s analysis. Used for the dev banner and could
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
