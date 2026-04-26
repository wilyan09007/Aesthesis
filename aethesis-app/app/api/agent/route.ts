import { runWithAIContext } from "@auth0/ai-vercel"
import { getSession } from "@/lib/auth0"
import { upsertUser } from "@/lib/db/runs"
import {
  buildExecuteTool,
  createEphemeralThread,
  getAssistantId,
  getOrCreateThreadForRun,
  runLoop,
} from "@/lib/backboard"

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

      // Persist a Backboard thread per saved run so chat history survives
      // restarts. Falls back to an ephemeral thread for unsaved runs.
      const bbThreadId = currentRunId
        ? await getOrCreateThreadForRun(assistantId, currentRunId)
        : await createEphemeralThread(assistantId)

      const executeTool = buildExecuteTool({ user, currentRunId })
      const reply = await runLoop(bbThreadId, message, executeTool)
      return Response.json({ reply })
    } catch (err) {
      console.error("[agent]", err)
      return Response.json({ error: String(err) }, { status: 500 })
    }
  })
}

