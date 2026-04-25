"use client"

import { useState, useCallback } from "react"
import { AnimatePresence, motion } from "framer-motion"
import Landing from "@/components/Landing"
import CaptureView from "@/components/CaptureView"
import AssessView from "@/components/AssessView"
import AnalyzingView from "@/components/AnalyzingView"
import ResultsView from "@/components/ResultsView"
import type { AppState, AnalyzeResponse, CaptureInputs, VideoFiles } from "@/lib/types"
import { MOCK_DATA } from "@/lib/mockData"

const pageVariants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -12 },
}

const pageTransition = { duration: 0.3, ease: [0.4, 0, 0.2, 1] as [number, number, number, number] }

export default function Home() {
  const [state, setState] = useState<AppState>("landing")
  const [captureInputs, setCaptureInputs] = useState<CaptureInputs | null>(null)
  const [videoFiles, setVideoFiles] = useState<VideoFiles>({ a: null, b: null })
  const [analyzeResponse, setAnalyzeResponse] = useState<AnalyzeResponse | null>(null)

  const goCapture = useCallback(() => setState("capture"), [])
  const goAssess = useCallback(() => setState("assess"), [])

  const goAnalyzing = useCallback((files: VideoFiles) => {
    setVideoFiles(files)
    setState("analyzing")
  }, [])

  const goResults = useCallback(() => {
    setAnalyzeResponse(MOCK_DATA)
    setState("results")
  }, [])

  const reset = useCallback(() => {
    setState("landing")
    setCaptureInputs(null)
    setVideoFiles({ a: null, b: null })
    setAnalyzeResponse(null)
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
              onAnalyze={goAnalyzing}
              onBack={() => setState(captureInputs ? "capture" : "landing")}
            />
          </motion.div>
        )}

        {state === "analyzing" && (
          <motion.div key="analyzing" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <AnalyzingView videoFiles={videoFiles} onComplete={goResults} />
          </motion.div>
        )}

        {state === "results" && analyzeResponse && (
          <motion.div key="results" variants={pageVariants} initial="initial" animate="animate" exit="exit" transition={pageTransition}>
            <ResultsView data={analyzeResponse} videoFiles={videoFiles} onReset={reset} />
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  )
}
