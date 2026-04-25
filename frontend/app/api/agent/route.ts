import { runWithAIContext } from "@auth0/ai-vercel"
import { getSession } from "@/lib/auth0"
import { upsertUser, listRuns, compareRuns } from "@/lib/db/runs"
import { prisma } from "@/lib/prisma"

const BASE = "https://app.backboard.io/api"

const SYSTEM = `You are a neural UX intelligence agent for Aesthesis. Your sole purpose is to help users understand and compare their brain-response analysis runs.

You have access to the user's analysis history — each run is a per-second neural simulation derived from Meta's TRIBE v2 model (451 hours of fMRI data, 720+ humans).

ROI signals: aesthetic_appeal, visual_fluency, cognitive_load, trust_affinity, reward_anticipation, motor_readiness, surprise_novelty, friction_anxiety

STRICT SCOPE RULE: You ONLY answer questions about the user's Aesthesis runs, ROI signals, UX insights, and comparisons between runs. If a question is unrelated to runs or UX analysis (e.g. general knowledge, coding, weather, anything else), respond with exactly: "I can only help with your Aesthesis analysis runs. Ask me to compare runs, identify trends, or explain your ROI signals."

When comparing runs:
1. Call list_past_runs to see what's available
2. Call compare_runs with the most relevant past run
3. Synthesise the delta into clear UX insights, highlighting the ROIs that changed most

When asked about trends across multiple runs:
1. Call get_run_trends for aggregate stats
2. Identify consistent patterns, recurring weaknesses, and improvements over time
3. Connect signal patterns to likely product experience causes

Memory is enabled — you remember facts from previous conversations with this user. Use that context proactively.`

const TOOL_DEFS = [
  {
    type: "function",
    function: {
      name: "list_past_runs",
      description: "List the user's saved analysis runs with summaries. Call this first.",
      parameters: {
        type: "object",
        properties: {
          limit: { type: "number", description: "Number of runs to return (1-20, default 10)" },
        },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "compare_runs",
      description:
        "Compare the current run against a past run. Returns per-ROI deltas (positive = current run is higher).",
      parameters: {
        type: "object",
        properties: {
          pastRunId: { type: "string", description: "ID of the past run to compare against" },
        },
        required: ["pastRunId"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_run_insights",
      description: "Get the full timestamped insights for a specific past run.",
      parameters: {
        type: "object",
        properties: {
          runId: { type: "string", description: "The run ID" },
        },
        required: ["runId"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_run_trends",
      description:
        "Aggregate all saved runs to detect trends over time: which ROIs are improving, which are consistently weak, and how signals shift across sessions. Use this for questions like 'what keeps appearing across my runs?' or 'am I improving over time?'",
      parameters: {
        type: "object",
        properties: {},
        required: [],
      },
    },
  },
]

async function bb(path: string, body?: unknown): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "X-API-Key": process.env.BACKBOARD_API_KEY!,
      "Content-Type": "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

// Persists the Backboard assistant ID in AppConfig so it survives server restarts
async function getAssistantId(): Promise<string> {
  const row = await prisma.appConfig.findUnique({ where: { key: "backboard_assistant_id" } })
  if (row) return row.value

  const res = await bb("/assistants", {
    name: "Aesthesis Neural Intelligence Agent",
    system_prompt: SYSTEM,
    tools: TOOL_DEFS,
  })
  const data = await res.json()
  console.log("[agent] create assistant:", JSON.stringify(data).slice(0, 200))
  if (!data.assistant_id) throw new Error(`Failed to create assistant: ${JSON.stringify(data)}`)

  await prisma.appConfig.create({ data: { key: "backboard_assistant_id", value: data.assistant_id } })
  return data.assistant_id
}

// Persists the Backboard thread ID on the RunSummary so conversations survive restarts.
// One Backboard thread per saved run — all sessions about the same run share context.
async function getOrCreateBackboardThread(assistantId: string, runId: string): Promise<string> {
  const summary = await prisma.runSummary.findUnique({
    where: { runId },
    select: { backboardThreadId: true },
  })

  if (summary?.backboardThreadId) return summary.backboardThreadId

  const res = await bb(`/assistants/${assistantId}/threads`, {})
  const data = await res.json()
  const threadId: string = data.thread_id

  await prisma.runSummary.update({
    where: { runId },
    data: { backboardThreadId: threadId },
  })

  return threadId
}

type BackboardResponse = {
  status: "IN_PROGRESS" | "REQUIRES_ACTION" | "COMPLETED" | "FAILED" | "CANCELLED" | null
  content: string | null
  run_id: string
  tool_calls: Array<{ id: string; type: string; function: { name: string; arguments: string } }> | null
}

async function runLoop(
  bbThreadId: string,
  message: string,
  executeTool: (name: string, args: Record<string, unknown>) => Promise<unknown>,
): Promise<string> {
  let res: BackboardResponse = await bb(`/threads/${bbThreadId}/messages`, {
    content: message,
    stream: false,
    memory: "Auto",
  }).then((r) => r.json())
  console.log("[agent] message response:", JSON.stringify(res))

  const submittedIds = new Set<string>()
  let iterations = 0

  while (res.status === "REQUIRES_ACTION" && iterations < 8) {
    iterations++

    // Backboard returns the full history of tool calls in each response.
    // Only process IDs we haven't submitted yet.
    const newCalls = (res.tool_calls ?? []).filter((c) => !submittedIds.has(c.id))
    console.log(`[agent] iter ${iterations} new calls:`, newCalls.map((c) => c.function.name))

    if (newCalls.length === 0) break

    const outputs = await Promise.all(
      newCalls.map(async (call) => {
        submittedIds.add(call.id)
        const args = JSON.parse(call.function.arguments || "{}")
        const result = await executeTool(call.function.name, args)
        console.log(`[agent] tool ${call.function.name}:`, JSON.stringify(result).slice(0, 150))
        return { tool_call_id: call.id, output: JSON.stringify(result) }
      }),
    )

    res = await fetch(`${BASE}/threads/${bbThreadId}/runs/${res.run_id}/submit-tool-outputs`, {
      method: "POST",
      headers: { "X-API-Key": process.env.BACKBOARD_API_KEY!, "Content-Type": "application/json" },
      body: JSON.stringify({ tool_outputs: outputs }),
    }).then((r) => r.json())
    console.log("[agent] submit response status:", res.status, "content:", res.content?.slice(0, 100) ?? null)
  }
  console.log("[agent] final:", res.status, "reply length:", res.content?.length ?? 0)

  if (res.status === "FAILED" || res.status === "CANCELLED") {
    throw new Error(`Backboard run ${res.status}`)
  }

  return res.content ?? ""
}

export async function POST(req: Request) {
  const session = await getSession()
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 })
  }

  const { message, currentRunId, threadId } = (await req.json()) as {
    message: string
    currentRunId?: string
    threadId: string
  }

  const user = await upsertUser(
    session.user.sub,
    session.user.email ?? session.user.sub,
    session.user.name,
  )

  // runWithAIContext scopes tool execution to this thread — Auth0 AI pattern
  return runWithAIContext({ threadID: threadId }, async () => {
    try {
      const assistantId = await getAssistantId()

      // Use currentRunId as the Backboard thread key so the same run always
      // gets the same thread, surviving server restarts.
      // Fall back to ephemeral threadId if the run isn't saved yet.
      const bbThreadId = currentRunId
        ? await getOrCreateBackboardThread(assistantId, currentRunId)
        : await (async () => {
            const res = await bb(`/assistants/${assistantId}/threads`, {})
            return (await res.json()).thread_id as string
          })()

      const executeTool = async (name: string, args: Record<string, unknown>) => {
        if (name === "list_past_runs") {
          const limit = typeof args.limit === "number" ? args.limit : 10
          const runs = await listRuns(user.id, limit)
          return runs.map((r) => ({
            id: r.id,
            goal: r.goal,
            createdAt: r.createdAt,
            winner: r.summary?.winner ?? null,
            summarySnippet: r.summary?.summaryText?.slice(0, 120) ?? null,
          }))
        }

        if (name === "compare_runs") {
          if (!currentRunId) return { error: "Current run not saved — ask the user to click Save first." }
          const result = await compareRuns(currentRunId, args.pastRunId as string, user.id)
          return result ?? { error: "Could not load comparison — one or both runs may be missing." }
        }

        if (name === "get_run_insights") {
          const run = await prisma.run.findFirst({
            where: { id: args.runId as string, userId: user.id },
            include: { insights: { orderBy: { timestampStart: "asc" } }, summary: true },
          })
          if (!run) return { error: "Run not found" }
          return {
            goal: run.goal,
            summary: run.summary?.summaryText,
            insights: run.insights.map((ins) => ({
              range: [ins.timestampStart, ins.timestampEnd],
              observation: ins.uxObservation,
              recommendation: ins.recommendation,
            })),
          }
        }

        if (name === "get_run_trends") {
          const runs = await prisma.run.findMany({
            where: { userId: user.id, status: "COMPLETE" },
            orderBy: { createdAt: "asc" },
            include: { summary: true },
          })

          if (runs.length === 0) return { error: "No completed runs found." }

          const ROI_KEYS = [
            "aesthetic_appeal", "visual_fluency", "cognitive_load", "trust_affinity",
            "reward_anticipation", "motor_readiness", "surprise_novelty", "friction_anxiety",
          ] as const

          // Per-ROI averages across all runs
          const roiSums: Record<string, number[]> = Object.fromEntries(ROI_KEYS.map(k => [k, []]))
          for (const run of runs) {
            if (!run.summary) continue
            const avg = run.summary.avgRoiA as Record<string, number>
            for (const k of ROI_KEYS) roiSums[k].push(avg[k] ?? 0)
          }

          const overallAvg = Object.fromEntries(
            ROI_KEYS.map(k => {
              const vals = roiSums[k]
              return [k, vals.length ? +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(3) : null]
            })
          )

          // First vs last run delta (improvement over time)
          const firstRoi = runs.find(r => r.summary)?.summary?.avgRoiA as Record<string, number> | undefined
          const lastRoi = [...runs].reverse().find(r => r.summary)?.summary?.avgRoiA as Record<string, number> | undefined
          const improvement = firstRoi && lastRoi
            ? Object.fromEntries(ROI_KEYS.map(k => [k, +((lastRoi[k] ?? 0) - (firstRoi[k] ?? 0)).toFixed(3)]))
            : null

          const weakest = [...ROI_KEYS].sort((a, b) => (overallAvg[a] ?? 0) - (overallAvg[b] ?? 0)).slice(0, 3)
          const strongest = [...ROI_KEYS].sort((a, b) => (overallAvg[b] ?? 0) - (overallAvg[a] ?? 0)).slice(0, 3)

          return {
            totalRuns: runs.length,
            dateRange: { first: runs[0].createdAt, last: runs[runs.length - 1].createdAt },
            overallAverageRoi: overallAvg,
            persistentWeaknesses: weakest,
            consistentStrengths: strongest,
            improvementSinceFirst: improvement,
            recentRuns: runs.slice(-5).map(r => ({
              id: r.id,
              createdAt: r.createdAt,
              goal: r.goal,
              avgRoi: r.summary?.avgRoiA,
            })),
          }
        }

        return { error: `Unknown tool: ${name}` }
      }

      const reply = await runLoop(bbThreadId, message, executeTool)
      return Response.json({ reply })
    } catch (err) {
      console.error("[agent]", err)
      return Response.json({ error: String(err) }, { status: 500 })
    }
  })
}
