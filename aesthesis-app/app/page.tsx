"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import Landing from "@/components/Landing"
import CaptureView from "@/components/CaptureView"
import AssessView from "@/components/AssessView"
import AnalyzingView from "@/components/AnalyzingView"
import ResultsView from "@/components/ResultsView"
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
  const [state, setState] = useState<AppState>("landing")
  const [captureInputs, setCaptureInputs] = useState<CaptureInputs | null>(null)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [results, setResults] = useState<ResultsViewData | null>(null)
  const [analyzeError, setAnalyzeError] = useState<string | null>(null)

  // Abort controller — if the user clicks back / starts a new run while a
  // request is in flight, we cancel it. /api/analyze can run for ~13s; an
  // orphaned fetch would write into stale UI state.
  const abortRef = useRef<AbortController | null>(null)

  const goCapture = useCallback(() => setState("capture"), [])
  const goAssess = useCallback(() => setState("assess"), [])

  const launchAnalysis = useCallback(
    async (file: File, goal: string | null) => {
      if (!file) {
        setAnalyzeError("A video is required.")
        return
      }
      setVideoFile(file)
      setResults(null)
      setAnalyzeError(null)
      setState("analyzing")

      // Cancel any in-flight request before starting a new one.
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac

      // eslint-disable-next-line no-console
      console.info("[aesthesis] launching analysis", {
        api: API_BASE_URL,
        size: file.size,
        goal_present: !!goal,
      })

      try {
        const raw = await analyze({ file, goal, signal: ac.signal })
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

  // When the analyzing UI finishes its progress animation AND the response
  // has arrived, transition to results. AnalyzingView calls onComplete when
  // its synthetic progress hits 100%. If the network finished first, we
  // already have results; if not, we wait for it.
  const handleAnalyzeProgressComplete = useCallback(() => {
    if (results) {
      setState("results")
    }
  }, [results])

  // If results arrive AFTER progress has already completed, advance.
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
              onContinue={(inputs) => {
                setCaptureInputs(inputs)
                goAssess()
              }}
              onBack={() => setState("landing")}
            />
          </motion.div>
        )}

        {state === "assess" && (
          <motion.div key="assess" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AssessView
              captureInputs={captureInputs}
              onAnalyze={(file) =>
                launchAnalysis(file, captureInputs?.goal ?? null)
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
              onRetry={() => videoFile && launchAnalysis(videoFile, captureInputs?.goal ?? null)}
              onCancel={reset}
              isResolved={results !== null}
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
