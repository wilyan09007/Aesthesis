// Aesthesis backend client.
//
// Public API: `analyze({file, goal, signal}) => Promise<AnalyzeResponse>`.
// The signature is unchanged from the synchronous-fetch days, so callers
// in `app/page.tsx`, `AnalyzingView.tsx`, etc. don't have to change. The
// internals switched from one long-running fetch to a spawn + poll
// flow because Modal's web proxy enforces a hard ~150s sync timeout
// on web endpoints — long V-JEPA inferences (real videos, ~80–150s)
// were tripping it and the browser saw `TypeError: Failed to fetch`
// even though Tribe was still happily processing.
//
// New protocol:
//   1. POST /api/analyze   → returns { job_id, run_id, status: "queued" } in <1s
//   2. GET  /api/analyze/status/{job_id}  → poll every ~3s
//      → { status: "running" }                           (keep polling)
//      → { status: "done", result: AnalyzeResponse }     (resolve)
//      → { status: "failed", error: string }             (reject)
//      → { status: "expired", error: string }            (reject; re-run)
//
// Verbose console logging is intentional. Every log line includes
// run_id (when known) and job_id so you can grep across browser and
// Modal logs.

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

// Polling cadence + ceiling. 3s is fast enough that the user perceives
// progress without hammering Modal. 8min ceiling sits ~2min under the
// background function's 600s timeout — if we hit this without seeing
// "done", something's structurally wrong upstream and we'd rather fail
// loudly than wait forever.
const POLL_INTERVAL_MS = 3000
const POLL_TIMEOUT_MS = 8 * 60 * 1000

function logScope(rid: string | null, jobId: string | null = null) {
  const parts: string[] = []
  if (rid) parts.push(rid.slice(0, 8))
  if (jobId) parts.push(jobId.slice(0, 12))
  const tag = parts.length > 0 ? `[aesthesis:${parts.join(":")}]` : "[aesthesis]"
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

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"))
      return
    }
    const t = setTimeout(resolve, ms)
    signal?.addEventListener("abort", () => {
      clearTimeout(t)
      reject(new DOMException("Aborted", "AbortError"))
    }, { once: true })
  })
}

async function _readErrorBody(resp: Response): Promise<unknown> {
  try {
    return await resp.json()
  } catch {
    try {
      return await resp.text()
    } catch {
      return null
    }
  }
}

function _extractDetailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in (body as object)) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === "string") return detail
    if (detail && typeof detail === "object") {
      const v = detail as ValidationFailure
      if (v.field && v.error) return `${v.field}: ${v.error}`
    }
  }
  return fallback
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
  log.info("analyze.spawn.begin", {
    url,
    file: { name: file.name, size: file.size, type: file.type },
    goal_present: !!(goal && goal.trim()),
  })

  const t0 = performance.now()

  // ── Step 1: kick off the background job ────────────────────────────────
  let spawnResp: Response
  try {
    spawnResp = await fetch(url, {
      method: "POST",
      body: fd,
      signal,
      // Browser sets multipart Content-Type with the right boundary.
      // Don't set it manually — that breaks the boundary.
      cache: "no-store",
    })
  } catch (e) {
    const elapsed_ms = Math.round(performance.now() - t0)
    log.error("analyze.spawn.network_failure", { elapsed_ms, error: String(e) })
    throw new AnalyzeError(
      `Network error reaching ${url}: ${String(e)}. ` +
        `Is the backend running? (uvicorn aesthesis.main:app --port 8000)`,
      0,
      null,
      null,
    )
  }

  const spawnElapsedMs = Math.round(performance.now() - t0)
  if (!spawnResp.ok) {
    const body = await _readErrorBody(spawnResp)
    log.error("analyze.spawn.http_error", {
      status: spawnResp.status,
      elapsed_ms: spawnElapsedMs,
      body,
    })
    const message = _extractDetailMessage(
      body, `Backend returned ${spawnResp.status}`,
    )
    throw new AnalyzeError(message, spawnResp.status, body, null)
  }

  const spawn = (await spawnResp.json()) as {
    job_id: string
    run_id: string
    status: string
  }
  const rid = spawn.run_id ?? spawnResp.headers.get("X-Aesthesis-Run-Id")
  const jobId = spawn.job_id ?? spawnResp.headers.get("X-Aesthesis-Job-Id")
  const scoped = logScope(rid, jobId)
  scoped.info("analyze.spawn.ok", {
    elapsed_ms: spawnElapsedMs,
    initial_status: spawn.status,
  })

  if (!jobId) {
    throw new AnalyzeError(
      "Backend did not return a job_id — check the orchestrator deploy.",
      500,
      spawn,
      rid,
    )
  }

  // ── Step 2: poll status until done or failed ───────────────────────────
  const statusUrl = `${API_BASE_URL}/api/analyze/status/${encodeURIComponent(jobId)}`
  let pollCount = 0

  while (true) {
    if (signal?.aborted) {
      throw new AnalyzeError("Analyze aborted by caller.", 0, null, rid)
    }
    if (performance.now() - t0 > POLL_TIMEOUT_MS) {
      throw new AnalyzeError(
        `Analyze did not complete in ${Math.round(POLL_TIMEOUT_MS / 1000)}s.`,
        504,
        null,
        rid,
      )
    }

    pollCount += 1
    let pollResp: Response
    try {
      pollResp = await fetch(statusUrl, { cache: "no-store", signal })
    } catch (e) {
      // Transient network blip during polling — log and retry on next
      // cycle. Don't fail the whole analyze on one missed poll.
      scoped.warn("analyze.poll.network_failure", {
        poll: pollCount, error: String(e),
      })
      try {
        await sleep(POLL_INTERVAL_MS, signal)
      } catch {
        throw new AnalyzeError("Analyze aborted by caller.", 0, null, rid)
      }
      continue
    }

    if (!pollResp.ok) {
      // Status endpoint shouldn't return non-2xx for a normal job
      // lifecycle (404=unknown job, 410=expired). Anything else is a
      // real backend problem; bubble it up.
      const body = await _readErrorBody(pollResp)
      // Special case: 410 Gone for expired results — surface as a
      // dedicated message so the user knows to re-run.
      if (pollResp.status === 410) {
        const msg = _extractDetailMessage(body, "Result expired — re-run analyze.")
        throw new AnalyzeError(msg, 410, body, rid)
      }
      scoped.error("analyze.poll.http_error", {
        poll: pollCount, status: pollResp.status, body,
      })
      const message = _extractDetailMessage(
        body, `Status poll returned ${pollResp.status}`,
      )
      throw new AnalyzeError(message, pollResp.status, body, rid)
    }

    const data = (await pollResp.json()) as {
      status: string
      result?: AnalyzeResponse
      error?: string
      retry_after_s?: number
    }

    if (data.status === "done") {
      const elapsed_ms = Math.round(performance.now() - t0)
      scoped.info("analyze.done", {
        elapsed_ms,
        polls: pollCount,
        n_insights: data.result?.insights?.length ?? 0,
        n_events: data.result?.events?.length ?? 0,
      })
      if (!data.result) {
        throw new AnalyzeError(
          "Backend reported done but returned no result.",
          500,
          data,
          rid,
        )
      }
      return data.result
    }

    if (data.status === "failed" || data.status === "expired") {
      scoped.error("analyze.poll.terminal", {
        status: data.status,
        polls: pollCount,
        error: data.error,
      })
      throw new AnalyzeError(
        data.error ?? `Analyze ${data.status}.`,
        data.status === "expired" ? 410 : 500,
        data,
        rid,
      )
    }

    // status="queued" or "running" — keep polling.
    if (pollCount % 5 === 0) {
      // Throttle the keep-alive log so the console doesn't fill up.
      scoped.info("analyze.poll.waiting", {
        poll: pollCount,
        status: data.status,
        elapsed_ms: Math.round(performance.now() - t0),
      })
    }

    try {
      await sleep(POLL_INTERVAL_MS, signal)
    } catch {
      throw new AnalyzeError("Analyze aborted by caller.", 0, null, rid)
    }
  }
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

// Fire-and-forget Tribe GPU prewarm. Hits the orchestrator's /api/warmup,
// which now sends a tiny dummy MP4 to /process_video_timeline so V-JEPA
// weights actually load (the previous version pinged /health, which woke
// the container but didn't load the model). Without this, a cold first
// analyze paid the model-load tax in-band.
export function prewarmTribe(): void {
  fetch(`${API_BASE_URL}/api/warmup`, { cache: "no-store" }).catch(() => {
    /* fire-and-forget — failure here just means the analyze call eats the cold start */
  })
}
