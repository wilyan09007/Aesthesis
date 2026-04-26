"use client"

import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { wsUrl } from "@/lib/api"
import type { WSMessage } from "@/lib/types"

interface LiveStreamPanelProps {
  runId: string
  /** Fires once when backend sends `prewarm_ready` — the subprocess is
   *  warm, frames are flowing, and the Start button can be armed. */
  onPrewarmReady?: (info: { run_id: string; cdp_port: number }) => void
  /** Fires once when backend sends `capture_complete`. Carries info from
   *  the WS message so the parent can stash it alongside the run_id. */
  onCaptureComplete?: (info: { run_id: string; duration_s: number; mp4_size_bytes: number; n_actions: number }) => void
  /** Fires when backend sends `capture_failed`. Parent typically routes
   *  to an error UI with retry + cached-demo fallback (D29). */
  onCaptureFailed?: (info: { run_id: string; reason: string; message: string }) => void
}

type FpsTier = "low" | "medium" | "high"

const ACCENT = "#7C9CFF"

/** D30a — playback cadence. 100ms = 10fps display rate (matches T0 source).
 *  Decoupling render cadence from frame-arrival jitter is what eliminates
 *  perceived stutter even when the source is steady. */
const PAINT_INTERVAL_MS = 100

/** D30a — jitter buffer depth. Drop-stale policy keeps latency bounded. */
const FRAME_QUEUE_MAX = 3

/**
 * Live capture stream panel.
 *
 * Wire protocol (D30c):
 *   - Binary WS messages carry raw JPEG bytes (one frame each)
 *   - JSON WS messages carry control: stream_degraded, capture_complete,
 *     capture_failed, agent_event
 *
 * Pipeline (D30a + D30b):
 *   incoming binary frame
 *     -> ArrayBuffer queue (max 3, drop stale)
 *     -> rAF tick @ 100ms cadence
 *     -> createImageBitmap (off-main-thread JPEG decode)
 *     -> ctx.drawImage to <canvas>
 *     -> bitmap.close() (release GPU)
 *
 * D32: the backend immediately replays the last lifecycle event on
 * connect. We handle that the same way as fresh events — handlers are
 * idempotent (capture_complete/failed only fires the parent callback
 * once per run_id).
 */
export default function LiveStreamPanel({
  runId,
  onPrewarmReady,
  onCaptureComplete,
  onCaptureFailed,
}: LiveStreamPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const queueRef = useRef<ArrayBuffer[]>([])
  const lastPaintRef = useRef<number>(0)
  const rafIdRef = useRef<number | null>(null)
  const prewarmFiredRef = useRef<boolean>(false)
  const completionFiredRef = useRef<boolean>(false)
  const failureFiredRef = useRef<boolean>(false)

  const [connected, setConnected] = useState(false)
  const [degraded, setDegraded] = useState(false)
  const [fps, setFps] = useState(0)
  const frameCountRef = useRef(0)
  const lastFpsCheck = useRef(Date.now())

  // ── WebSocket lifecycle ───────────────────────────────────────────────
  useEffect(() => {
    if (!runId) return

    const url = wsUrl(`/api/stream/${runId}`)
    const ws = new WebSocket(url)
    ws.binaryType = "arraybuffer"
    // eslint-disable-next-line no-console
    console.info("[aesthesis:capture] ws.opening", { url, runId })

    ws.onopen = () => {
      setConnected(true)
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] ws.open", { runId })
    }

    ws.onmessage = (e) => {
      // D30c — branch on payload type
      if (typeof e.data === "string") {
        let msg: WSMessage
        try {
          msg = JSON.parse(e.data) as WSMessage
        } catch (parseErr) {
          // eslint-disable-next-line no-console
          console.warn("[aesthesis:capture] ws.json_parse_failed", { runId, parseErr, raw: e.data.slice(0, 200) })
          return
        }
        handleControl(msg)
      } else if (e.data instanceof ArrayBuffer) {
        enqueueFrame(e.data)
      } else if (e.data instanceof Blob) {
        // Some browsers default to Blob even with binaryType=arraybuffer
        // in certain configs; handle it defensively.
        e.data.arrayBuffer().then(enqueueFrame).catch((err) => {
          // eslint-disable-next-line no-console
          console.warn("[aesthesis:capture] ws.blob_to_arraybuffer_failed", { runId, err })
        })
      } else {
        // eslint-disable-next-line no-console
        console.warn("[aesthesis:capture] ws.unknown_payload_type", { runId, type: typeof e.data })
      }
    }

    ws.onerror = (ev) => {
      // eslint-disable-next-line no-console
      console.warn("[aesthesis:capture] ws.error", { runId, ev })
      setConnected(false)
    }

    ws.onclose = (ev) => {
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] ws.close", { runId, code: ev.code, reason: ev.reason })
      setConnected(false)
    }

    return () => {
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] ws.cleanup", { runId })
      try { ws.close() } catch { /* swallow */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  // ── rAF paint loop (D30a + D30b) ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false

    const tick = (now: number) => {
      if (cancelled) return

      if (queueRef.current.length > 0 && now - lastPaintRef.current >= PAINT_INTERVAL_MS) {
        const buf = queueRef.current.shift()!
        lastPaintRef.current = now
        // Off-main-thread JPEG decode via createImageBitmap
        const blob = new Blob([buf], { type: "image/jpeg" })
        createImageBitmap(blob)
          .then((bitmap) => {
            const c = canvasRef.current
            if (!c) { bitmap.close(); return }
            // Resize canvas to match bitmap dims for crisp rendering.
            // (Avoids browser bilinear filtering when dims match.)
            if (c.width !== bitmap.width) c.width = bitmap.width
            if (c.height !== bitmap.height) c.height = bitmap.height
            const ctx = c.getContext("2d")
            if (!ctx) { bitmap.close(); return }
            ctx.drawImage(bitmap, 0, 0)
            bitmap.close()

            // FPS counter (display-rate observed by user)
            frameCountRef.current++
            const tnow = Date.now()
            const elapsed = tnow - lastFpsCheck.current
            if (elapsed >= 1000) {
              setFps(Math.round((frameCountRef.current * 1000) / elapsed))
              frameCountRef.current = 0
              lastFpsCheck.current = tnow
            }
            if (degraded) setDegraded(false)
          })
          .catch((err) => {
            // eslint-disable-next-line no-console
            console.warn("[aesthesis:capture] paint.decode_failed", { runId, err })
          })
      }

      rafIdRef.current = requestAnimationFrame(tick)
    }

    rafIdRef.current = requestAnimationFrame(tick)

    return () => {
      cancelled = true
      if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current)
    }
  }, [runId, degraded])

  // ── Helpers ───────────────────────────────────────────────────────────

  function enqueueFrame(buf: ArrayBuffer) {
    queueRef.current.push(buf)
    if (queueRef.current.length > FRAME_QUEUE_MAX) {
      // Drop stale — prefer fresh frames over caught-up buffer
      queueRef.current.shift()
    }
  }

  function handleControl(msg: WSMessage) {
    if (msg.type === "stream_degraded") {
      // eslint-disable-next-line no-console
      console.warn("[aesthesis:capture] ws.stream_degraded", { runId })
      setDegraded(true)
      return
    }

    if (msg.type === "prewarm_ready") {
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] ws.prewarm_ready", { runId, ...msg })
      // D32 — backend replays this on reconnect; gate so the parent only
      // sees one fire per run_id.
      if (!prewarmFiredRef.current) {
        prewarmFiredRef.current = true
        onPrewarmReady?.({ run_id: msg.run_id, cdp_port: msg.cdp_port })
      }
      return
    }

    if (msg.type === "capture_complete") {
      // eslint-disable-next-line no-console
      console.info("[aesthesis:capture] ws.capture_complete", { runId, ...msg })
      // D32 — backend replays this on reconnect; gate so the parent only
      // sees one fire per run_id.
      if (!completionFiredRef.current) {
        completionFiredRef.current = true
        onCaptureComplete?.({
          run_id: msg.run_id,
          duration_s: msg.duration_s,
          mp4_size_bytes: msg.mp4_size_bytes,
          n_actions: msg.n_actions,
        })
      }
      return
    }

    if (msg.type === "capture_failed") {
      // eslint-disable-next-line no-console
      console.error("[aesthesis:capture] ws.capture_failed", { runId, ...msg })
      if (!failureFiredRef.current) {
        failureFiredRef.current = true
        onCaptureFailed?.({
          run_id: msg.run_id,
          reason: msg.reason,
          message: msg.message,
        })
      }
      return
    }

    if (msg.type === "agent_event") {
      // Reserved channel; silent in v1.1
      return
    }

    if (msg.type === "frame" && "frame_b64" in msg) {
      // Legacy text-frame variant — backend never sends this in v1.1+.
      // Decode for back-compat with older fixtures.
      try {
        const bin = atob(msg.frame_b64)
        const buf = new ArrayBuffer(bin.length)
        const view = new Uint8Array(buf)
        for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i)
        enqueueFrame(buf)
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[aesthesis:capture] legacy_frame_decode_failed", { runId, err })
      }
    }
  }

  const fpsTier: FpsTier = fps >= 8 ? "high" : fps >= 4 ? "medium" : "low"
  const fpsTierColor = fpsTier === "high" ? "#5CF2C5" : fpsTier === "medium" ? "#FBBF24" : "#FF6B6B"

  return (
    <div className="relative rounded-xl overflow-hidden aspect-video panel">
      {/* Stream header */}
      <div className="absolute top-3 left-3 right-3 flex items-center justify-between z-20">
        <div className="flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: ACCENT }} />
          <span style={{ color: ACCENT }}>Live</span>
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

      {/* Canvas frame target. width/height get set on first paint to match
          the source bitmap dims; CSS makes it letterbox into the panel. */}
      <canvas
        ref={canvasRef}
        className="w-full h-full"
        style={{ display: connected ? "block" : "none", objectFit: "contain", background: "black" }}
      />

      {/* Waiting state */}
      {!connected && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <motion.div
              className="w-8 h-8 rounded-full mx-auto mb-3"
              style={{ border: "2px solid rgba(124,156,255,0.3)", borderTopColor: ACCENT }}
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
