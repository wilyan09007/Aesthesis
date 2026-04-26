"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { useUser } from "@auth0/nextjs-auth0/client"
import Landing from "@/components/Landing"
import CaptureView from "@/components/CaptureView"
import AssessView from "@/components/AssessView"
import AnalyzingView from "@/components/AnalyzingView"
import ResultsView from "@/components/ResultsView"
import HistoryPanel from "@/components/HistoryPanel"
import ComparePanel from "@/components/ComparePanel"
import AgentPanel from "@/components/AgentPanel"
import { analyze, AnalyzeError, API_BASE_URL } from "@/lib/api"
import { adaptForResultsView, type ResultsViewData } from "@/lib/adapt"
import type { AppState, CaptureInputs } from "@/lib/types"

const pageVariants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -12 },
}

const pageTransition = { duration: 0.3, ease: [0.4, 0, 0.2, 1] as [number, number, number, number] }

export default function Home() {
  const { user } = useUser()

  const [state, setState] = useState<AppState>("landing")
  const [captureInputs, setCaptureInputs] = useState<CaptureInputs | null>(null)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [results, setResults] = useState<ResultsViewData | null>(null)
  const [analyzeError, setAnalyzeError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  // ── DEMO ESCAPE HATCH — visit /?demo to jump straight to the results
  // page with a fixture. Delete this block + lib/demoResults.ts to revert.
  useEffect(() => {
    if (typeof window === "undefined") return
    if (!new URLSearchParams(window.location.search).has("demo")) return
    import("@/lib/demoResults").then(({ demoAnalyzeResponse }) => {
      setResults(adaptForResultsView(demoAnalyzeResponse))
      setState("results")
    })
  }, [])
  // ── /DEMO ESCAPE HATCH

  const [historyOpen, setHistoryOpen] = useState(false)
  const [savedRunId, setSavedRunId] = useState<string | null>(null)
  const [compareRunId, setCompareRunId] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle")
  const [agentOpen, setAgentOpen] = useState(false)

  const requireAuth = useCallback((action: () => void) => {
    if (!user) {
      window.location.href = "/api/auth/login"
      return
    }
    action()
  }, [user])

  const goCapture = useCallback(() => requireAuth(() => setState("capture")), [requireAuth])
  const goAssess = useCallback(() => requireAuth(() => setState("assess")), [requireAuth])

  const launchAnalysis = useCallback(async (file: File, goal: string | null) => {
    if (!file) { setAnalyzeError("A video is required."); return }
    setVideoFile(file)
    setResults(null)
    setAnalyzeError(null)
    setSavedRunId(null)
    setSaveStatus("idle")
    setState("analyzing")

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    // eslint-disable-next-line no-console
    console.info("[aesthesis] launching analysis", { api: API_BASE_URL, size: file.size, goal_present: !!goal })

    try {
      const raw = await analyze({ file, goal, signal: ac.signal })
      if (ac.signal.aborted) return
      setResults(adaptForResultsView(raw))
    } catch (e) {
      if (ac.signal.aborted) return
      const msg = e instanceof AnalyzeError
        ? `${e.message}${e.runId ? ` (run_id ${e.runId})` : ""}`
        : e instanceof Error ? e.message : String(e)
      setAnalyzeError(msg)
    }
  }, [])

  const handleAnalyzeProgressComplete = useCallback(() => {
    if (results) setState("results")
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
    setSavedRunId(null)
    setSaveStatus("idle")
    setCompareRunId(null)
  }, [])

  const handleSave = useCallback(async (): Promise<string> => {
    if (!results || !user) throw new Error("Not ready to save")
    setSaveStatus("saving")
    try {
      const runRes = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: captureInputs?.goal ?? null, urlA: captureInputs?.url ?? null }),
      })
      if (!runRes.ok) {
        const err = await runRes.json().catch(() => ({}))
        throw new Error(err.error ?? `POST /api/runs failed: ${runRes.status}`)
      }
      const { id: runId } = await runRes.json()

      const sumRes = await fetch(`/api/runs/${runId}/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(results.raw),
      })
      if (!sumRes.ok) {
        const err = await sumRes.json().catch(() => ({}))
        throw new Error(err.error ?? `POST summary failed: ${sumRes.status}`)
      }

      setSavedRunId(runId)
      setSaveStatus("saved")
      return runId
    } catch (err) {
      setSaveStatus("error")
      console.error("[handleSave]", err)
      throw err
    }
  }, [results, captureInputs, user])

  const handleSaveOrReturn = useCallback(async (): Promise<string> => {
    if (savedRunId) return savedRunId
    return handleSave()
  }, [savedRunId, handleSave])

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
              onContinue={(inputs) => { setCaptureInputs(inputs); goAssess() }}
              onBack={() => setState("landing")}
            />
          </motion.div>
        )}

        {state === "assess" && (
          <motion.div key="assess" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AssessView
              captureInputs={captureInputs}
              onAnalyze={(file) => launchAnalysis(file, captureInputs?.goal ?? null)}
              onBack={() => setState(captureInputs ? "capture" : "landing")}
            />
          </motion.div>
        )}

        {state === "analyzing" && (
          <motion.div key="analyzing" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AnalyzingView
              onComplete={handleAnalyzeProgressComplete}
              error={analyzeError}
              onRetry={() => videoFile && launchAnalysis(videoFile, captureInputs?.goal ?? null)}
              onCancel={reset}
              isResolved={results !== null}
            />
          </motion.div>
        )}

        {state === "results" && results && (
          <motion.div key="results" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <ResultsView
              data={results}
              videoFile={videoFile}
              onReset={reset}
              savedRunId={savedRunId}
              saveStatus={saveStatus}
              onSave={user ? handleSave : undefined}
              onHistoryOpen={user ? () => setHistoryOpen(true) : undefined}
              onAgentOpen={user ? () => {
                // Auto-save so the agent has a runId to compare against.
                // Opens the panel immediately; by the time the user types,
                // the save is complete and currentRunIdRef picks up the new id.
                if (!savedRunId) handleSave().catch(console.error)
                setAgentOpen(true)
              } : undefined}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <HistoryPanel
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        savedRunId={savedRunId}
        onSaveFirst={handleSaveOrReturn}
        onCompare={(pastRunId) => setCompareRunId(pastRunId)}
      />

      {compareRunId && savedRunId && (
        <ComparePanel
          currentRunId={savedRunId}
          pastRunId={compareRunId}
          onClose={() => setCompareRunId(null)}
        />
      )}

      <AnimatePresence>
        {agentOpen && (
          <AgentPanel
            currentRunId={savedRunId}
            onClose={() => setAgentOpen(false)}
          />
        )}
      </AnimatePresence>
    </main>
  )
}
