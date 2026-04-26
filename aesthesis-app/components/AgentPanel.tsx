"use client"

import { useRef, useState } from "react"
import { motion } from "framer-motion"
import { useUser } from "@auth0/nextjs-auth0/client"

interface AgentPanelProps {
  currentRunId: string | null
  onClose: () => void
}

type Message = { role: "user" | "assistant"; content: string }

const SUGGESTIONS = [
  "Compare this run with my best past run",
  "What changed most since my last analysis?",
  "Which ROI signals improved the most?",
  "List all my saved runs",
]

export default function AgentPanel({ currentRunId, onClose }: AgentPanelProps) {
  const { user } = useUser()
  const threadId = useRef(`thread_${Date.now()}`).current
  const currentRunIdRef = useRef(currentRunId)
  currentRunIdRef.current = currentRunId
  const bottomRef = useRef<HTMLDivElement>(null)

  const [messages, setMessages] = useState<Message[]>([])
  const [inputValue, setInputValue] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const send = async (text: string) => {
    const msg = text.trim()
    if (!msg || loading) return
    setInputValue("")
    setError(null)
    setMessages((prev) => [...prev, { role: "user", content: msg }])
    setLoading(true)
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50)

    try {
      const res = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, currentRunId: currentRunIdRef.current, threadId }),
      })
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    send(inputValue)
  }

  return (
    <motion.div
      className="fixed bottom-6 right-6 z-50 flex flex-col rounded-2xl overflow-hidden"
      style={{
        width: 420,
        height: 560,
        background: "rgba(0,0,0,0.98)",
        border: "1px solid rgba(224,69,77,0.2)",
        backdropFilter: "blur(24px)",
        boxShadow: "0 24px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(224,69,77,0.08)",
      }}
      initial={{ opacity: 0, y: 20, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 20, scale: 0.96 }}
      transition={{ duration: 0.2 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 shrink-0"
        style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full flex items-center justify-center"
            style={{ background: "rgba(224,69,77,0.15)", border: "1px solid rgba(224,69,77,0.3)" }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#E0454D" strokeWidth="1.5">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 8v4M12 16h.01" />
            </svg>
          </div>
          <div>
            <p className="text-xs font-medium" style={{ color: "#e8eaf0" }}>Neural Intelligence Agent</p>
            <p className="text-[10px]" style={{ color: "rgba(255,255,255,0.35)" }}>
              Backboard · Auth0 AI · {user?.name ?? user?.email ?? ""}
              {!currentRunId && " · save run to compare"}
            </p>
          </div>
        </div>
        <button onClick={onClose} className="p-1.5 rounded-lg"
          style={{ color: "rgba(255,255,255,0.35)" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e8eaf0")}
          onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.35)")}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="flex flex-col gap-2 mt-2">
            <p className="text-xs text-center mb-2" style={{ color: "rgba(255,255,255,0.3)" }}>
              Ask me to compare this run against your history
            </p>
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)}
                className="text-left px-3 py-2.5 rounded-xl text-xs transition-all"
                style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", color: "rgba(255,255,255,0.5)" }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = "rgba(224,69,77,0.25)"; e.currentTarget.style.color = "#E0454D" }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)"; e.currentTarget.style.color = "rgba(255,255,255,0.5)" }}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className="max-w-[85%] px-3.5 py-2.5 rounded-xl text-xs leading-relaxed whitespace-pre-wrap"
              style={
                msg.role === "user"
                  ? { background: "rgba(224,69,77,0.15)", border: "1px solid rgba(224,69,77,0.2)", color: "#e8eaf0" }
                  : { background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)", color: "rgba(255,255,255,0.82)" }
              }
            >
              {msg.content}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="px-3.5 py-2.5 rounded-xl"
              style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="flex gap-1 items-center h-3">
                {[0, 1, 2].map((i) => (
                  <motion.div key={i} className="w-1.5 h-1.5 rounded-full"
                    style={{ background: "#E0454D" }}
                    animate={{ opacity: [0.3, 1, 0.3] }}
                    transition={{ duration: 1, repeat: Infinity, delay: i * 0.2 }}
                  />
                ))}
              </div>
            </div>
          </div>
        )}

        {error && (
          <p className="text-xs text-center px-3 py-2 rounded-xl"
            style={{ color: "#FF6B6B", background: "rgba(255,107,107,0.08)", border: "1px solid rgba(255,107,107,0.2)" }}>
            {error}
          </p>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="shrink-0 px-4 pb-4 pt-3"
        style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="flex items-center gap-2">
          <input
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Ask about your runs…"
            className="flex-1 px-3.5 py-2.5 rounded-xl text-xs outline-none"
            style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)", color: "#e8eaf0" }}
            onFocus={e => (e.currentTarget.style.borderColor = "rgba(224,69,77,0.4)")}
            onBlur={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)")}
            disabled={loading}
          />
          <button type="submit" disabled={!inputValue.trim() || loading}
            className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all"
            style={{
              background: inputValue.trim() && !loading ? "rgba(224,69,77,0.2)" : "rgba(255,255,255,0.04)",
              border: `1px solid ${inputValue.trim() && !loading ? "rgba(224,69,77,0.35)" : "rgba(255,255,255,0.08)"}`,
              color: inputValue.trim() && !loading ? "#E0454D" : "rgba(255,255,255,0.2)",
            }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          </button>
        </div>
      </form>
    </motion.div>
  )
}
