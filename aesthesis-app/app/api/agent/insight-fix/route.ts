import { runWithAIContext } from "@auth0/ai-vercel"
import { getSession } from "@/lib/auth0"
import { upsertUser } from "@/lib/db/runs"
import {
  buildExecuteTool,
  createEphemeralThread,
  getAssistantId,
  runLoop,
} from "@/lib/backboard"
import type { Insight } from "@/lib/types"

function buildPrompt(insight: Insight, goal: string | null): string {
  const [t0, t1] = insight.timestamp_range_s
  const goalLine = goal ? `\nUser's stated goal for this video: ${goal}\n` : ""
  return `A user is reviewing one specific UX insight from an Aesthesis neural-response analysis. Generate a tactical, actionable fix tailored to this insight.
${goalLine}
Time range: ${t0.toFixed(1)}s – ${t1.toFixed(1)}s
Observation: ${insight.ux_observation}
Initial recommendation from the analysis: ${insight.recommendation}
Cited brain features: ${insight.cited_brain_features.join(", ") || "(none)"}
Screen moment: ${insight.cited_screen_moment || "(unspecified)"}

Your job:
1. Suggest a specific, concrete fix the user could implement — name exact UI elements, copy adjustments, design patterns, or interaction changes. Avoid generic UX platitudes.
2. If you can, call list_past_runs and/or get_run_trends to check whether this is a recurring pattern in the user's history; if it is, mention it briefly and tailor the fix.
3. Keep the response to 4–6 short bullet points or 2–3 short paragraphs total. Be tactical, not abstract. Skip preambles and disclaimers.`
}

export async function POST(req: Request) {
  const session = await getSession()
  if (!session?.user) {
    return Response.json({ error: "Sign in to get personalized fix suggestions." }, { status: 401 })
  }

  const { insight, currentRunId, goal, threadId } = (await req.json()) as {
    insight: Insight
    currentRunId?: string | null
    goal?: string | null
    threadId: string
  }

  if (!insight || !Array.isArray(insight.timestamp_range_s)) {
    return Response.json({ error: "Invalid insight payload." }, { status: 400 })
  }

  const user = await upsertUser(
    session.user.sub,
    session.user.email ?? session.user.sub,
    session.user.name,
  )

  return runWithAIContext({ threadID: threadId }, async () => {
    try {
      const assistantId = await getAssistantId()
      // One-shot ephemeral thread per insight request — keeps these focused
      // suggestions out of the persistent chat thread for the run.
      const bbThreadId = await createEphemeralThread(assistantId)
      const message = buildPrompt(insight, goal ?? null)
      const executeTool = buildExecuteTool({ user, currentRunId: currentRunId ?? null })
      const suggestion = await runLoop(bbThreadId, message, executeTool)
      return Response.json({ suggestion })
    } catch (err) {
      console.error("[insight-fix]", err)
      return Response.json({ error: String(err) }, { status: 500 })
    }
  })
}
