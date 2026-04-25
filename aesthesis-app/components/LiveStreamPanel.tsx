"use client"

import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { WSMessage } from "@/lib/types"

interface LiveStreamPanelProps {
  runId: string
  version: "A" | "B"
}

type FpsTier = "low" | "medium" | "high"

export default function LiveStreamPanel({ runId, version }: LiveStreamPanelProps) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [degraded, setDegraded] = useState(false)
  const [connected, setConnected] = useState(false)
  const [fps, setFps] = useState(0)
  const frameCountRef = useRef(0)
  const lastFpsCheck = useRef(Date.now())

  useEffect(() => {
    const ws = new WebSocket(`/api/stream/${runId}`)

    ws.onopen = () => setConnected(true)

    ws.onmessage = (e) => {
      const msg: WSMessage = JSON.parse(e.data)

      if (msg.type === "frame" && msg.version === version) {
        if (imgRef.current) {
          imgRef.current.src = `data:image/jpeg;base64,${msg.frame_b64}`
        }
        frameCountRef.current++
        const now = Date.now()
        const elapsed = now - lastFpsCheck.current
        if (elapsed >= 1000) {
          setFps(Math.round((frameCountRef.current * 1000) / elapsed))
          frameCountRef.current = 0
          lastFpsCheck.current = now
        }
        if (degraded) setDegraded(false)
      }

      if (msg.type === "stream_degraded" && msg.version === version) {
        setDegraded(true)
      }
    }

    ws.onerror = () => setConnected(false)
    ws.onclose = () => setConnected(false)

    return () => ws.close()
  }, [runId, version, degraded])

  const fpsTier: FpsTier = fps >= 8 ? "high" : fps >= 4 ? "medium" : "low"
  const fpsTierColor = fpsTier === "high" ? "#5CF2C5" : fpsTier === "medium" ? "#FBBF24" : "#FF6B6B"

  return (
    <div className="relative flex-1 rounded-xl overflow-hidden aspect-video panel">
      {/* Stream header */}
      <div className="absolute top-3 left-3 right-3 flex items-center justify-between z-20">
        <div className="flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <span style={{ color: "rgba(255,255,255,0.5)" }}>Version</span>
          <span style={{ color: version === "A" ? "#7C9CFF" : "#5CF2C5" }}>{version}</span>
        </div>

        <div className="flex items-center gap-2">
          {connected && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs"
              style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <span style={{ color: fpsTierColor }}>{fps}</span>
              <span style={{ color: "rgba(255,255,255,0.35)" }}>fps</span>
              <div className="w-1.5 h-1.5 rounded-full" style={{ background: fpsTierColor, animation: "pulse-glow 1.5s infinite" }} />
            </div>
          )}
        </div>
      </div>

      {/* Image frame */}
      <img
        ref={imgRef}
        alt={`Live stream version ${version}`}
        className="w-full h-full object-cover"
        style={{ display: connected ? "block" : "none" }}
      />

      {/* Waiting state */}
      {!connected && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <motion.div
              className="w-8 h-8 rounded-full mx-auto mb-3"
              style={{ border: "2px solid rgba(124,156,255,0.3)", borderTopColor: "#7C9CFF" }}
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
            />
            <p className="text-xs" style={{ color: "rgba(255,255,255,0.35)" }}>Connecting…</p>
          </div>
        </div>
      )}

      {/* Degraded overlay */}
      <AnimatePresence>
        {degraded && (
          <motion.div
            className="absolute inset-0 flex items-center justify-center"
            style={{ background: "rgba(11,15,20,0.75)", backdropFilter: "blur(4px)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="text-center">
              <div className="w-8 h-8 mx-auto mb-2" style={{ color: "#FBBF24" }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
              </div>
              <p className="text-xs font-medium" style={{ color: "#FBBF24" }}>Stream degraded</p>
              <p className="text-xs mt-1" style={{ color: "rgba(255,255,255,0.35)" }}>Reconnecting…</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}