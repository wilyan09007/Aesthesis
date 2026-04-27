"use client"

import { lazy, Suspense, useEffect } from "react"
import { motion } from "framer-motion"
import AuthButton from "./AuthButton"
import { prewarmTribe } from "@/lib/api"

// Hero brain — same cortical visualization the results page uses, driven
// by a client-side synthetic activation sequence. Lazy so the heavy three.js
// + GLB chunks don't block first paint of the hero copy.
const BrainHero = lazy(() => import("./BrainHero"))

interface LandingProps {
  onSkipToAssess: () => void
}

const FEATURES = [
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
      </svg>
    ),
    title: "Neural precision",
    body: "8 interpretable brain signals tracked per second across the full session — not aggregates.",
  },
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </svg>
    ),
    title: "Timestamped clarity",
    body: "Every observation anchors to a specific second. Click it on the timeline — the video seeks there.",
  },
  {
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
        <line x1="8" y1="21" x2="16" y2="21" />
        <line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    ),
    title: "Demo anything",
    body: "Drop any screen recording — landing page, signup flow, dashboard, mobile app. The pipeline reads it.",
  },
]

const STEPS = [
  { n: "01", title: "Input", body: "Enter a URL or upload an MP4 of your demo." },
  { n: "02", title: "Capture", body: "Autonomous agents navigate and record the experience (or skip if you have a recording)." },
  { n: "03", title: "Encode", body: "TRIBE v2 predicts neural response per second of footage." },
  { n: "04", title: "Read", body: "Timestamped insights, neural metrics, and an overall assessment." },
]

export default function Landing({ onSkipToAssess }: LandingProps) {
  // Wake the Tribe GPU container while the user is reading the hero — the
  // cold start (~30-60s) finishes before they click through to upload, so
  // /api/analyze hits a warm Tribe and stays under the 150s proxy ceiling.
  useEffect(() => { prewarmTribe() }, [])

  return (
    <div className="relative min-h-screen overflow-x-hidden" style={{
      backgroundImage: "radial-gradient(rgba(255,255,255,0.028) 1px, transparent 1px)",
      backgroundSize: "44px 44px",
    }}>
      {/* Nav bar */}
      <nav className="relative z-20 flex items-center justify-between px-10 py-5"
        style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-full flex items-center justify-center"
            style={{ background: "rgba(224,69,77,0.15)", border: "1px solid rgba(224,69,77,0.3)" }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#E0454D" strokeWidth="1.5">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
          </div>
          <span className="text-sm font-medium tracking-wide" style={{ color: "#e8eaf0" }}>Aesthesis</span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1 text-xs" style={{ color: "rgba(255,255,255,0.3)" }}>
            <span>Powered by</span>
            <span className="font-medium" style={{ color: "rgba(255,255,255,0.5)" }}>TRIBE v2</span>
          </div>
          <AuthButton />
        </div>
      </nav>

      {/* ── HERO ─────────────────────────────────────────────────── */}
      <section className="relative z-10 grid grid-cols-2 min-h-[calc(100vh-57px)] items-center gap-0">

        {/* Left: copy */}
        <div className="px-14 py-16 flex flex-col gap-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7 }}
          >
            <div className="inline-flex items-center gap-2 mb-7 px-3 py-1 rounded-full text-[11px] tracking-widest uppercase"
              style={{ background: "rgba(224,69,77,0.08)", border: "1px solid rgba(224,69,77,0.2)", color: "#E0454D" }}>
              <span className="w-1 h-1 rounded-full inline-block" style={{ background: "#E0454D" }} />
              Neural UX intelligence
            </div>

            <h1 className="font-light leading-[1.08] tracking-tight mb-6"
              style={{ fontSize: "clamp(3rem, 5vw, 4.25rem)", color: "#e8eaf0" }}>
              Demo anything.<br />
              <span style={{ color: "#E0454D" }}>See the analysis.</span>
            </h1>

            <p className="text-base leading-relaxed max-w-md" style={{ color: "rgba(255,255,255,0.52)" }}>
              Aesthesis reads any screen recording through a simulated neural response — derived from TRIBE v2, Meta&apos;s foundation model trained on 451 hours of fMRI data from 720+ humans. Not surveys. Not heatmaps. Exactly when attention, friction, and intent fired, timestamped to the second.
            </p>
          </motion.div>

          {/* Feature chips */}
          <motion.div
            className="flex flex-col gap-3"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.15 }}
          >
            {FEATURES.map((f) => (
              <div key={f.title} className="flex items-start gap-3">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                  style={{ background: "rgba(224,69,77,0.1)", color: "#E0454D" }}>
                  {f.icon}
                </div>
                <div>
                  <span className="text-sm font-medium" style={{ color: "#e8eaf0" }}>{f.title}</span>
                  <span className="text-sm ml-2" style={{ color: "rgba(255,255,255,0.4)" }}>{f.body}</span>
                </div>
              </div>
            ))}
          </motion.div>

          {/* CTAs */}
          <motion.div
            className="flex gap-3 pt-2"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.28 }}
          >
            <motion.button
              onClick={onSkipToAssess}
              className="flex items-center gap-2.5 px-5 py-3 rounded-xl text-sm font-medium"
              style={{
                background: "rgba(224,69,77,0.18)",
                border: "1px solid rgba(224,69,77,0.3)",
                color: "#e8eaf0",
              }}
              whileHover={{ scale: 1.03, boxShadow: "0 0 28px rgba(224,69,77,0.2)" }}
              whileTap={{ scale: 0.98 }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#E0454D" strokeWidth="2">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              Upload MP4
            </motion.button>
          </motion.div>
        </div>

        {/* Right: Brain3D hero */}
        <div className="relative flex items-center justify-center h-full">
          {/* Decorative rings */}
          <motion.div className="absolute w-[660px] h-[660px] rounded-full"
            style={{ border: "1px solid rgba(224,69,77,0.08)" }}
            animate={{ rotate: 360 }}
            transition={{ duration: 90, repeat: Infinity, ease: "linear" }}
          />
          <motion.div className="absolute w-[490px] h-[490px] rounded-full"
            style={{ border: "1px solid rgba(224,69,77,0.06)" }}
            animate={{ rotate: -360 }}
            transition={{ duration: 60, repeat: Infinity, ease: "linear" }}
          />
          <div className="absolute w-[350px] h-[350px] rounded-full"
            style={{ border: "1px solid rgba(255,255,255,0.04)" }} />

          {/* Brain */}
          <motion.div
            initial={{ opacity: 0, scale: 0.88 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 1.1, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
          >
            <Suspense fallback={<BrainFallback />}>
              <BrainHero size={540} />
            </Suspense>
          </motion.div>

          {/* Floating ROI badges */}
          <motion.div
            className="absolute text-[10px] font-mono px-2 py-1 rounded-lg"
            style={{
              top: "22%", right: "10%",
              background: "rgba(224,69,77,0.12)",
              border: "1px solid rgba(224,69,77,0.25)",
              color: "#E0454D",
            }}
            animate={{ y: [0, -6, 0] }}
            transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
          >
            reward_anticipation ↑
          </motion.div>
          <motion.div
            className="absolute text-[10px] font-mono px-2 py-1 rounded-lg"
            style={{
              bottom: "26%", left: "8%",
              background: "rgba(139,92,246,0.12)",
              border: "1px solid rgba(167,139,250,0.32)",
              color: "#a78bfa",
              boxShadow: "0 0 22px rgba(139,92,246,0.32), 0 0 48px rgba(139,92,246,0.14)",
            }}
            animate={{ y: [0, 6, 0] }}
            transition={{ duration: 5, repeat: Infinity, ease: "easeInOut", delay: 1.5 }}
          >
            friction_anxiety ↓
          </motion.div>
          <motion.div
            className="absolute text-[10px] font-mono px-2 py-1 rounded-lg"
            style={{
              top: "60%", right: "6%",
              background: "rgba(224,69,77,0.1)",
              border: "1px solid rgba(224,69,77,0.22)",
              color: "#E0454D",
            }}
            animate={{ y: [0, -4, 0] }}
            transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut", delay: 0.8 }}
          >
            trust_affinity ↑
          </motion.div>
        </div>
      </section>

      {/* ── HOW IT WORKS ─────────────────────────────────────────── */}
      <section className="relative z-10 px-14 py-20 max-w-5xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.7 }}
        >
          <p className="text-[11px] tracking-widest uppercase mb-10"
            style={{ color: "rgba(255,255,255,0.28)" }}>
            How it works
          </p>

          <div className="flex items-start gap-0">
            {STEPS.map((step, i) => (
              <div key={step.n} className="flex items-start flex-1">
                <div className="flex flex-col gap-3 flex-1">
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-mono" style={{ color: "rgba(224,69,77,0.5)" }}>{step.n}</span>
                    {i < STEPS.length - 1 && (
                      <div className="flex-1 h-px" style={{ background: "rgba(255,255,255,0.08)" }} />
                    )}
                  </div>
                  <div className="pr-8">
                    <p className="text-sm font-medium mb-1.5" style={{ color: "#e8eaf0" }}>{step.title}</p>
                    <p className="text-xs leading-relaxed" style={{ color: "rgba(255,255,255,0.38)" }}>{step.body}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      </section>

      {/* ── ACTION CARD ──────────────────────────────────────────── */}
      <section className="relative z-10 px-14 pb-24 max-w-xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.7 }}
        >
          <LandingCard
            label="Upload your demo"
            description="Drop an MP4 recording of any product flow and get instant neural analysis."
            badge="Upload path"
            badgeColor="#E0454D"
            glowColor="rgba(224,69,77,0.12)"
            borderHoverColor="rgba(224,69,77,0.35)"
            onClick={onSkipToAssess}
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            }
          />
        </motion.div>
      </section>
    </div>
  )
}

function BrainFallback() {
  return (
    <div className="flex items-center justify-center" style={{ width: 540, height: 540 }}>
      <motion.div
        className="w-20 h-20 rounded-full"
        style={{ border: "2px solid rgba(224,69,77,0.2)", borderTopColor: "#E0454D" }}
        animate={{ rotate: 360 }}
        transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}
      />
    </div>
  )
}

interface LandingCardProps {
  label: string
  description: string
  badge: string
  badgeColor: string
  glowColor: string
  borderHoverColor: string
  onClick: () => void
  icon: React.ReactNode
}

function LandingCard({ label, description, badge, badgeColor, glowColor, borderHoverColor, onClick, icon }: LandingCardProps) {
  return (
    <motion.button
      onClick={onClick}
      className="relative flex-1 p-7 rounded-2xl text-left cursor-pointer group"
      style={{
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.08)",
        backdropFilter: "blur(16px)",
      }}
      whileHover={{ scale: 1.02, transition: { duration: 0.18 } }}
      initial={false}
    >
      <motion.div
        className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
        style={{ boxShadow: `0 0 40px ${glowColor}, inset 0 0 40px ${glowColor}`, border: `1px solid ${borderHoverColor}` }}
      />
      <div className="mb-4" style={{ color: badgeColor }}>{icon}</div>
      <div className="inline-flex mb-2.5 px-2 py-0.5 rounded text-[10px] tracking-widest uppercase font-medium"
        style={{ background: `${badgeColor}14`, color: badgeColor }}>
        {badge}
      </div>
      <h2 className="text-lg font-medium mb-2" style={{ color: "#e8eaf0" }}
        dangerouslySetInnerHTML={{ __html: label }} />
      <p className="text-sm leading-relaxed" style={{ color: "rgba(255,255,255,0.42)" }}>{description}</p>
      <motion.div className="mt-5 flex items-center gap-1 text-sm font-medium" style={{ color: badgeColor }}
        initial={{ x: 0 }} whileHover={{ x: 4 }}>
        Get started
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M5 12h14M12 5l7 7-7 7" />
        </svg>
      </motion.div>
    </motion.button>
  )
}
