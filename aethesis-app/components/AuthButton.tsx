"use client"

import { useUser } from "@auth0/nextjs-auth0/client"
import { motion } from "framer-motion"

export default function AuthButton() {
  const { user, isLoading } = useUser()

  if (isLoading) {
    return (
      <div className="w-7 h-7 rounded-full"
        style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.08)" }} />
    )
  }

  if (!user) {
    return (
      <motion.a
        href="/api/auth/login"
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium"
        style={{
          background: "rgba(124,156,255,0.1)",
          border: "1px solid rgba(124,156,255,0.25)",
          color: "#7C9CFF",
        }}
        whileHover={{ scale: 1.03, boxShadow: "0 0 16px rgba(124,156,255,0.15)" }}
        whileTap={{ scale: 0.97 }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4M10 17l5-5-5-5M15 12H3" />
        </svg>
        Sign in
      </motion.a>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs"
        style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.5)" }}>
        {user.picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={user.picture} alt="" className="w-4 h-4 rounded-full" />
        ) : (
          <div className="w-4 h-4 rounded-full flex items-center justify-center text-[8px] font-bold"
            style={{ background: "rgba(124,156,255,0.3)", color: "#7C9CFF" }}>
            {(user.name ?? user.email ?? "?")[0].toUpperCase()}
          </div>
        )}
        <span>{user.name ?? user.email}</span>
      </div>
      <motion.a
        href="/api/auth/logout"
        className="px-2.5 py-1.5 rounded-lg text-xs transition-colors"
        style={{ color: "rgba(255,255,255,0.3)", border: "1px solid rgba(255,255,255,0.08)" }}
        whileHover={{ color: "#FF6B6B", borderColor: "rgba(255,107,107,0.25)" }}
      >
        Sign out
      </motion.a>
    </div>
  )
}
