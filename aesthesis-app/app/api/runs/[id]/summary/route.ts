import { NextResponse } from "next/server"
import { getSession } from "@/lib/auth0"
import { upsertUser, saveRunSummary } from "@/lib/db/runs"
import type { AnalyzeResponse } from "@/lib/types"

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession()
  if (!session?.user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const { id } = await params
  const body = (await req.json()) as AnalyzeResponse

  const user = await upsertUser(session.user.sub, session.user.email ?? session.user.sub, session.user.name)
  const summary = await saveRunSummary(id, user.id, body)
  if (!summary) return NextResponse.json({ error: "Not found" }, { status: 404 })

  return NextResponse.json({ ok: true, runId: id })
}
