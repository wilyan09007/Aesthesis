"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import Landing from "@/components/Landing"
import CaptureView, { type CaptureCompletePayload } from "@/components/CaptureView"
import AssessView from "@/components/AssessView"
import AnalyzingView from "@/components/AnalyzingView"
import ResultsView from "@/components/ResultsView"
import {
  analyze, analyzeByRunId, AnalyzeError, API_BASE_URL,
  fetchCapturedVideo,
} from "@/lib/api"
import { adaptForResultsView, type ResultsViewData } from "@/lib/adapt"
import type { AnalyzeResponse, AppState, CachedDemoEntry, CaptureInputs } from "@/lib/types"

const pageVariants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -12 },
}

const pageTransition = { duration: 0.3, ease: [0.4, 0, 0.2, 1] as [number, number, number, number] }

/** Grace seconds for the D11 capture-path confirm countdown. */
const CAPTURE_CONFIRM_S = 3

export default function Home() {
  const [state, setState] = useState<AppState>("landing")
  const [captureInputs, setCaptureInputs] = useState<CaptureInputs | null>(null)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [results, setResults] = useState<ResultsViewData | null>(null)
  const [analyzeError, setAnalyzeError] = useState<string | null>(null)

  // D11 — capture path bookkeeping
  const [captureRunId, setCaptureRunId] = useState<string | null>(null)
  const [cameFromCapture, setCameFromCapture] = useState<boolean>(false)
  const [pendingByRunGoal, setPendingByRunGoal] = useState<string | null>(null)

  // Abort controller — if the user clicks back / starts a new run while a
  // request is in flight, we cancel it. /api/analyze can run for ~13s; an
  // orphaned fetch would write into stale UI state.
  const abortRef = useRef<AbortController | null>(null)

  const goCapture = useCallback(() => setState("capture"), [])
  const goAssess = useCallback(() => setState("assess"), [])

  // ── Shared analyze runner ─────────────────────────────────────────────
  // Both upload (skip path) and by-run (capture path) call this with their
  // respective promise so the abort + state setup + result processing
  // logic stays in one place.
  const runAnalyzePromise = useCallback(
    async (promise: Promise<AnalyzeResponse>, ac: AbortController) => {
      try {
        const raw = await promise
        if (ac.signal.aborted) return
        setResults(adaptForResultsView(raw))
      } catch (e) {
        if (ac.signal.aborted) return
        const msg =
          e instanceof AnalyzeError
            ? `${e.message}${e.runId ? ` (run_id ${e.runId})` : ""}`
            : e instanceof Error
              ? e.message
              : String(e)
        setAnalyzeError(msg)
      }
    },
    [],
  )

  // ── Skip path: launch analysis from a user-uploaded MP4 ────────────────
  const launchAnalysisUpload = useCallback(
    async (file: File, goal: string | null) => {
      if (!file) {
        setAnalyzeError("A video is required.")
        return
      }
      setVideoFile(file)
      setResults(null)
      setAnalyzeError(null)
      setCameFromCapture(false)
      setCaptureRunId(null)
      setState("analyzing")

      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac

      // eslint-disable-next-line no-console
      console.info("[aesthesis] launchAnalysisUpload", {
        api: API_BASE_URL, size: file.size, goal_present: !!goal,
      })
      await runAnalyzePromise(
        analyze({ file, goal, signal: ac.signal }),
        ac,
      )
    },
    [runAnalyzePromise],
  )

  // ── Capture path step 3: fire by-run analyze (called from AnalyzingView
  //    after the 3s confirm countdown). ─────────────────────────────────
  const fireAnalyzeByRun = useCallback(async () => {
    if (!captureRunId) {
      setAnalyzeError("internal: no captureRunId set when fireAnalyzeByRun called")
      return
    }
    setResults(null)
    setAnalyzeError(null)
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    // eslint-disable-next-line no-console
    console.info("[aesthesis] fireAnalyzeByRun", {
      api: API_BASE_URL, runId: captureRunId, goal_present: !!pendingByRunGoal,
    })
    await runAnalyzePromise(
      analyzeByRunId({ runId: captureRunId, goal: pendingByRunGoal, signal: ac.signal }),
      ac,
    )
  }, [captureRunId, pendingByRunGoal, runAnalyzePromise])

  // ── Capture path step 1: capture finished, fetch MP4 + set up gate ────
  const handleCaptureComplete = useCallback(async (payload: CaptureCompletePayload) => {
    // eslint-disable-next-line no-console
    console.info("[aesthesis] handleCaptureComplete", payload)
    try {
      const blob = await fetchCapturedVideo(payload.run_id)
      const file = new File([blob], `${payload.run_id}.mp4`, { type: "video/mp4" })
      setVideoFile(file)
      setCaptureRunId(payload.run_id)
      setPendingByRunGoal(payload.goal)
      setCameFromCapture(true)
      setResults(null)
      setAnalyzeError(null)
      // Skip AssessView entirely on capture path (D11 + user instruction).
      // AnalyzingView shows the 3s confirm countdown then fires fireAnalyzeByRun().
      setState("analyzing")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      // eslint-disable-next-line no-console
      console.error("[aesthesis] fetchCapturedVideo_failed", { error: msg })
      setAnalyzeError(`Could not fetch captured video: ${msg}`)
      setState("analyzing")
    }
  }, [])

  // ── Capture path D29: user clicked "Use cached demo" after a failed run.
  //    Fetch the cached MP4 and route through the upload skip path. ──────
  const handleUseCachedDemo = useCallback(async (entry: CachedDemoEntry, goal: string | null) => {
    // eslint-disable-next-line no-console
    console.info("[aesthesis] handleUseCachedDemo", { entry, goal })
    try {
      const url = `${API_BASE_URL}/api/cached-demos/${encodeURIComponent(entry.mp4_filename)}`
      const resp = await fetch(url, { cache: "no-store" })
      if (!resp.ok) {
        throw new Error(`Failed to fetch cached demo: ${resp.status}`)
      }
      const blob = await resp.blob()
      const file = new File([blob], entry.mp4_filename, { type: "video/mp4" })
      await launchAnalysisUpload(file, goal)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setAnalyzeError(`Could not load cached demo: ${msg}`)
      setState("analyzing")
    }
  }, [launchAnalysisUpload])

  // ── Cancel during the 3s confirm countdown (D11). Return to capture for
  //    a re-run. ──────────────────────────────────────────────────────────
  const handleConfirmCancel = useCallback(() => {
    // eslint-disable-next-line no-console
    console.info("[aesthesis] confirm_cancelled — returning to capture")
    abortRef.current?.abort()
    setVideoFile(null)
    setCaptureRunId(null)
    setPendingByRunGoal(null)
    setCameFromCapture(false)
    setResults(null)
    setAnalyzeError(null)
    setState("capture")
  }, [])

  // ── Animation -> results transition ───────────────────────────────────
  const handleAnalyzeProgressComplete = useCallback(() => {
    if (results) {
      setState("results")
    }
  }, [results])

  useEffect(() => {
    if (state === "analyzing" && results) {
      const t = setTimeout(() => setState("results"), 250)
      return () => clearTimeout(t)
    }
  }, [state, results])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState("landing")
    setCaptureInputs(null)
    setVideoFile(null)
    setResults(null)
    setAnalyzeError(null)
    setCaptureRunId(null)
    setPendingByRunGoal(null)
    setCameFromCapture(false)
  }, [])

  return (
    <main style={{ background: "#0B0F14", minHeight: "100vh" }}>
      <AnimatePresence mode="wait">
        {state === "landing" && (
          <motion.div key="landing" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <Landing onCaptureAndAssess={goCapture} onSkipToAssess={goAssess} />
          </motion.div>
        )}

        {state === "capture" && (
          <motion.div key="capture" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <CaptureView
              onCaptureComplete={handleCaptureComplete}
              onUseCachedDemo={handleUseCachedDemo}
              onBack={() => setState("landing")}
            />
          </motion.div>
        )}

        {state === "assess" && (
          <motion.div key="assess" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AssessView
              captureInputs={captureInputs}
              onAnalyze={(file) =>
                launchAnalysisUpload(file, captureInputs?.goal ?? null)
              }
              onBack={() => setState(captureInputs ? "capture" : "landing")}
            />
          </motion.div>
        )}

        {state === "analyzing" && (
          <motion.div key="analyzing" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AnalyzingView
              videoFile={videoFile}
              onComplete={handleAnalyzeProgressComplete}
              error={analyzeError}
              onRetry={() => {
                if (cameFromCapture && captureRunId) {
                  fireAnalyzeByRun()
                } else if (videoFile) {
                  launchAnalysisUpload(videoFile, captureInputs?.goal ?? null)
                }
              }}
              onCancel={reset}
              isResolved={results !== null}
              confirmCountdownS={cameFromCapture ? CAPTURE_CONFIRM_S : undefined}
              onConfirm={cameFromCapture ? fireAnalyzeByRun : undefined}
              onConfirmCancel={cameFromCapture ? handleConfirmCancel : undefined}
            />
          </motion.div>
        )}

        {state === "results" && results && (
          <motion.div key="results" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <ResultsView data={results} videoFile={videoFile} onReset={reset} />
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  )
}
