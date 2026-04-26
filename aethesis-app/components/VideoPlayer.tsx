"use client"

import { useEffect, useRef } from "react"
import { motion } from "framer-motion"

interface VideoPlayerProps {
  file: File | null
  currentTime: number
  onTimeUpdate: (t: number) => void
}

const ACCENT = "#7C9CFF"

export default function VideoPlayer({ file, currentTime, onTimeUpdate }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const isSeekingRef = useRef(false)
  const objectUrl = useRef<string | null>(null)

  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    objectUrl.current = url
    if (videoRef.current) videoRef.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => {
    const video = videoRef.current
    if (!video || isSeekingRef.current) return
    if (Math.abs(video.currentTime - currentTime) > 0.2) {
      video.currentTime = currentTime
      // Freeze on the requested frame so the UI at that moment is visible.
      // The >0.2s gap tells us this seek is external (graph/insight click),
      // not the per-frame timeupdate that fires during normal playback.
      video.pause()
    }
  }, [currentTime])

  const handleTimeUpdate = () => {
    if (!videoRef.current) return
    isSeekingRef.current = false
    onTimeUpdate(videoRef.current.currentTime)
  }

  const handleSeeking = () => {
    isSeekingRef.current = true
  }

  return (
    // Cap the player at half the viewport in each dimension so it sits
    // side-by-side with the Brain3D column instead of dominating the row.
    // ``height: 50vh`` is explicit (not ``maxHeight``) so the inner video
    // area can use ``flex-1`` against a definite container — that's what
    // ``object-contain`` needs to letterbox correctly. ``maxWidth: 50vw``
    // is the upper-bound from the spec; the row still uses ``flex-1`` so
    // the brain column always fits beside it.
    //
    // Previously: outer had ``flex-1`` with no height cap, inner had
    // ``flex-1 aspect-video``. ``items-stretch`` on the parent section
    // plus the duelling flex/aspect rules made the frame grow to fill
    // the whole viewport.
    <div
      className="flex-1 min-w-0 rounded-xl overflow-hidden panel flex flex-col"
      style={{ maxWidth: "50vw", height: "50vh" }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="w-2 h-2 rounded-full" style={{ background: ACCENT }} />
        <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Demo recording</span>
      </div>

      {/* Video — fills remaining height; ``object-contain`` letterboxes. */}
      <div className="relative flex-1 min-h-0 bg-black">
        {file ? (
          <video
            ref={videoRef}
            className="w-full h-full object-contain"
            controls
            onTimeUpdate={handleTimeUpdate}
            onSeeking={handleSeeking}
            onSeeked={() => { isSeekingRef.current = false }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <div className="text-center">
              <motion.div
                className="text-5xl font-light mb-3"
                style={{ color: `${ACCENT}20` }}
                animate={{ opacity: [0.4, 0.7, 0.4] }}
                transition={{ duration: 3, repeat: Infinity }}
              >
                ·
              </motion.div>
              <p className="text-xs" style={{ color: "rgba(255,255,255,0.2)" }}>No video</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
