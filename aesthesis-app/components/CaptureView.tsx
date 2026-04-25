"use client"

import { useEffect, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import LiveStreamPanel from "./LiveStreamPanel"
import { AnalyzeError, fetchCachedDemos, startCaptureRun } from "@/lib/api"
import type { CachedDemoEntry } from "@/lib/types"

export type CaptureCompletePayload = {
  run_id: string
  goal: string | null
  duration_s: number
  mp4_size_bytes: number
}

interface CaptureViewProps {
  onCaptureComplete: (payload: CaptureCompletePayload) => void
  onUseCachedDemo: (entry: CachedDemoEntry, goal: string | null) => void
  onBack: () => void
}

type StartError =
  | { kind: "in_progress"; activeRunId: string | null; message: string }
  | { kind: "network"; message: string }
  | { kind: "other"; message: string }

type CaptureFailedState = {
  reason: "timeout" | "crashed" | "navigation_error" | "setup_error"
  message: string
}

const ACCENT = "#7C9CFF"
const FAIL_RED = "#FF6B6B"

export default function CaptureView({
  onCaptureComplete,
  onUseCachedDemo,
  onBack,
}: CaptureViewProps) {
  const [url, setUrl] = useState("")
  const [goal, setGoal] = useState("")
  const [runId, setRunId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<StartError | null>(null)
  const [failedState, setFailedState] = useState<CaptureFailedState | null>(null)
  const [cachedDemos, setCachedDemos] = useState<CachedDemoEntry[]>([])

  const canStart = url.trim().length > 0 && !starting && runId === null

  // D29 — load the stage-day fallback list once. Empty if backend has none.
  useEffect(() => {
    let cancelled = false
    fetchCachedDemos()
      .then((list) => {
        if (!cancelled) setCachedDemos(list)
      })
      .catch(() => {
        // fetchCachedDemos already swallows; this is belt-and-suspenders
      })
    return () => {
      cancelled = true
    }
  }, [])

  const matchingCachedDemo = cachedDemos.find((d) => d.url === url.trim())

  async function handleStart() {
    setStartError(null)
    setFailedState(null)
    setStarting(true)
    // eslint-disable-next-line no-console
    console.info("[aesthesis:capture] handleStart", { url, goal_present: !!goal.trim() })
    try {
      const resp = await startCaptureRun({ url: url.trim(), goal: goal.trim() || null })
      setRunId(resp.run_id)
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] run_started", { run_id: resp.run_id })
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[aesthesis:capture] start_failed", { e })
      if (e instanceof AnalyzeError && e.status === 409) {
        setStartError({
          kind: "in_progress",
          activeRunId: e.runId,
          message: e.message,
        })
      } else if (e instanceof AnalyzeError && e.status === 0) {
        setStartError({ kind: "network", message: e.message })
      } else {
        const msg = e instanceof Error ? e.message : String(e)
        setStartError({ kind: "other", message: msg })
      }
    } finally {
      setStarting(false)
    }
  }

  function handleCaptureComplete(info: { run_id: string; duration_s: number; mp4_size_bytes: number; n_actions: number }) {
    // eslint-disable-next-line no-console
    console.info("[aesthesis:capture] handleCaptureComplete", info)
    onCaptureComplete({
      run_id: info.run_id,
      goal: goal.trim() || null,
      duration_s: info.duration_s,
      mp4_size_bytes: info.mp4_size_bytes,
    })
  }

  function handleCaptureFailed(info: { run_id: string; reason: string; message: string }) {
    // eslint-disable-next-line no-console
    console.error("[aesthesis:capture] handleCaptureFailed", info)
    setFailedState({
      reason: info.reason as CaptureFailedState["reason"],
      message: info.message,
    })
  }

  function handleRetry() {
    setRunId(null)
    setFailedState(null)
    setStartError(null)
  }

  function handleUseCached() {
    if (!matchingCachedDemo) return
    onUseCachedDemo(matchingCachedDemo, goal.trim() || null)
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-8 py-5" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <button onClick={onBack} className="flex items-center gap-2 text-sm transition-colors"
          style={{ color: "rgba(255,255,255,0.4)" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
          onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.4)")}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          Back
        </button>

        <StepIndicator step={1} />

        <div className="w-16" />
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col max-w-3xl mx-auto w-full px-8 py-8 gap-8">
        {/* Inputs */}
        <motion.div
          className="panel rounded-2xl p-6"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h2 className="text-lg font-medium mb-5" style={{ color: "#e8eaf0" }}>Configure Capture</h2>
          <InputField
            label="Demo URL"
            placeholder="https://your-demo.example.com"
            value={url}
            onChange={setUrl}
            disabled={runId !== null}
          />
          <div className="h-3" />
          <InputField
            label="Goal (optional)"
            placeholder="e.g. complete the signup flow"
            value={goal}
            onChange={setGoal}
            disabled={runId !== null}
          />

          <div className="flex items-center gap-3 mt-5">
            <motion.button
              onClick={handleStart}
              disabled={!canStart}
              className="px-5 py-2.5 rounded-xl text-sm font-medium transition-opacity"
              style={{
                background: canStart ? "rgba(124,156,255,0.15)" : "rgba(255,255,255,0.04)",
                border: "1px solid rgba(124,156,255,0.25)",
                color: canStart ? ACCENT : "rgba(255,255,255,0.3)",
                cursor: canStart ? "pointer" : "not-allowed",
              }}
              whileHover={canStart ? { scale: 1.02 } : {}}
            >
              {starting ? "Starting…" : runId ? "Capturing…" : "Start Capture"}
            </motion.button>

            {runId && !failedState && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full" style={{ background: FAIL_RED, animation: "pulse-glow 1s infinite" }} />
                <span className="text-xs" style={{ color: "rgba(255,255,255,0.4)" }}>
                  Recording live session…  <span className="font-mono opacity-60">{runId.slice(0, 8)}</span>
                </span>
              </motion.div>
            )}
          </div>

          {/* Start-time errors (409 / network / other) */}
          <AnimatePresence>
            {startError && (
              <motion.div
                initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                className="mt-4 p-3 rounded-lg"
                style={{ background: "rgba(255,107,107,0.06)", border: `1px solid rgba(255,107,107,0.25)` }}
              >
                <p className="text-xs" style={{ color: FAIL_RED }}>
                  {startError.kind === "in_progress" ? (
                    <>A capture is already running on this backend{startError.activeRunId && (
                      <> — <span className="font-mono">{startError.activeRunId.slice(0, 8)}</span></>
                    )}. Wait for it to finish or contact the operator.</>
                  ) : startError.kind === "network" ? (
                    <>Network error reaching the backend. Is <code>uvicorn</code> running on :8000?</>
                  ) : (
                    <>Failed to start capture: {startError.message}</>
                  )}
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        {/* Live panel — mounted only after we have a real backend run_id */}
        {runId && !failedState && (
          <motion.div
            className="flex-1"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
          >
            <LiveStreamPanel
              runId={runId}
              onCaptureComplete={handleCaptureComplete}
              onCaptureFailed={handleCaptureFailed}
            />
          </motion.div>
        )}

        {/* Capture-failed UI with retry + cached demo fallback (D29) */}
        {failedState && (
          <motion.div
            className="flex-1 flex flex-col gap-4"
            initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
          >
            <div className="panel rounded-2xl p-5" style={{ borderColor: `rgba(255,107,107,0.25)` }}>
              <p className="text-xs uppercase tracking-widest mb-2" style={{ color: FAIL_RED }}>
                Capture failed ({failedState.reason})
              </p>
              <p className="text-sm leading-relaxed font-mono" style={{ color: "rgba(255,255,255,0.85)" }}>
                {failedState.message}
              </p>
              <div className="mt-4 flex gap-3 flex-wrap">
                <button
                  onClick={handleRetry}
                  className="px-4 py-2 rounded-lg text-xs font-medium"
                  style={{
                    background: "rgba(124,156,255,0.15)", border: "1px solid rgba(124,156,255,0.3)",
                    color: ACCENT, cursor: "pointer",
                  }}
                >
                  Retry capture
                </button>
                {matchingCachedDemo && (
                  <button
                    onClick={handleUseCached}
                    className="px-4 py-2 rounded-lg text-xs font-medium"
                    style={{
                      background: "rgba(92,242,197,0.1)", border: "1px solid rgba(92,242,197,0.3)",
                      color: "#5CF2C5", cursor: "pointer",
                    }}
                  >
                    Use cached demo: {matchingCachedDemo.label}
                  </button>
                )}
                <button
                  onClick={onBack}
                  className="px-4 py-2 rounded-lg text-xs font-medium"
                  style={{
                    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                    color: "rgba(255,255,255,0.6)", cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </motion.div>
        )}

        {/* Empty state — pre-start */}
        {!runId && !failedState && (
          <motion.div
            className="flex-1"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
          >
            <EmptyPanel />
          </motion.div>
        )}
      </div>
    </div>
  )
}

function InputField({ label, placeholder, value, onChange, disabled }: {
  label: string
  placeholder: string
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  return (
    <div>
      <label className="block text-xs mb-1.5 tracking-wide" style={{ color: "rgba(255,255,255,0.4)" }}>{label}</label>
      <input
        type="text"
        value={value}
        disabled={disabled}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-all"
        style={{
          background: "rgba(255,255,255,0.04)",
          border: `1px solid rgba(255,255,255,0.08)`,
          color: disabled ? "rgba(255,255,255,0.4)" : "#e8eaf0",
          cursor: disabled ? "not-allowed" : "text",
        }}
        onFocus={e => (e.currentTarget.style.borderColor = `${ACCENT}40`)}
        onBlur={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)")}
      />
    </div>
  )
}

function EmptyPanel() {
  return (
    <div className="rounded-xl panel flex items-center justify-center aspect-video">
      <div className="text-center">
        <p className="text-xs" style={{ color: "rgba(255,255,255,0.2)" }}>Stream will appear here</p>
      </div>
    </div>
  )
}

function StepIndicator({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2 text-xs" style={{ color: "rgba(255,255,255,0.35)" }}>
      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-medium`}
        style={{ background: step >= 1 ? "rgba(124,156,255,0.2)" : "rgba(255,255,255,0.06)", color: step >= 1 ? ACCENT : "rgba(255,255,255,0.3)" }}>
        1
      </div>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>—</span>
      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-medium`}
        style={{ background: step >= 2 ? "rgba(124,156,255,0.2)" : "rgba(255,255,255,0.06)", color: step >= 2 ? ACCENT : "rgba(255,255,255,0.3)" }}>
        2
      </div>
    </div>
  )
}
