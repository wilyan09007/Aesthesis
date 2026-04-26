"use client"

import { useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"

interface UploadZoneProps {
  file: File | null
  onFile: (file: File | null) => void
}

const ACCENT = "#E0454D"

export default function UploadZone({ file, onFile }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)

  const handleFile = (f: File) => {
    if (!f.type.startsWith("video/")) return
    onFile(f)
    const url = URL.createObjectURL(f)
    setPreview(url)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }

  const handleClear = () => {
    onFile(null)
    if (preview) URL.revokeObjectURL(preview)
    setPreview(null)
    if (inputRef.current) inputRef.current.value = ""
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="w-full">
      <AnimatePresence mode="wait">
        {!file ? (
          <motion.div
            key="dropzone"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="relative rounded-xl cursor-pointer transition-all duration-200"
            style={{
              border: `2px dashed ${dragging ? ACCENT : "rgba(255,255,255,0.1)"}`,
              background: dragging ? `${ACCENT}08` : "rgba(255,255,255,0.02)",
              minHeight: "240px",
            }}
            onDragEnter={() => setDragging(true)}
            onDragLeave={() => setDragging(false)}
            onDragOver={e => e.preventDefault()}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
          >
            <input ref={inputRef} type="file" accept="video/mp4,video/*" className="hidden" onChange={handleChange} />

            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6">
              <motion.div
                animate={{ y: dragging ? -4 : 0 }}
                transition={{ duration: 0.2 }}
                style={{ color: dragging ? ACCENT : "rgba(255,255,255,0.25)" }}
              >
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </motion.div>
              <div className="text-center">
                <p className="text-sm font-medium" style={{ color: dragging ? ACCENT : "rgba(255,255,255,0.6)" }}>
                  {dragging ? "Drop to upload" : "Drop your demo recording here"}
                </p>
                <p className="text-xs mt-1" style={{ color: "rgba(255,255,255,0.3)" }}>
                  or click to browse — MP4 / WebM / MOV
                </p>
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="preview"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            className="rounded-xl overflow-hidden"
            style={{ border: `1px solid ${ACCENT}30`, background: "rgba(0,0,0,0.3)" }}
          >
            {preview && (
              <video
                src={preview}
                className="w-full aspect-video object-cover"
                muted
                preload="metadata"
              />
            )}
            <div className="p-3 flex items-center justify-between"
              style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
              <div>
                <p className="text-xs font-medium truncate max-w-[280px]" style={{ color: "#e8eaf0" }}>{file.name}</p>
                <p className="text-xs mt-0.5" style={{ color: "rgba(255,255,255,0.35)" }}>{formatSize(file.size)}</p>
              </div>
              <button
                onClick={handleClear}
                className="p-1.5 rounded-lg transition-colors"
                style={{ color: "rgba(255,255,255,0.35)" }}
                onMouseEnter={e => (e.currentTarget.style.color = "#FF6B6B")}
                onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.35)")}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
