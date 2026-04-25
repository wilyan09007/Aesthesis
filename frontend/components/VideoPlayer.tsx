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
    <div className="flex-1 rounded-xl overflow-hidden panel flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="w-2 h-2 rounded-full" style={{ background: ACCENT }} />
        <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>Demo recording</span>
      </div>

      {/* Video */}
      <div className="relative flex-1 bg-black aspect-video">
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
