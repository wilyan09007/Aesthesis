import { NextResponse } from "next/server"
import { getSession } from "@/lib/auth0"
import { upsertUser, createRun, listRuns } from "@/lib/db/runs"

export async function GET() {
  const session = await getSession()
  if (!session?.user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const user = await upsertUser(session.user.sub, session.user.email ?? session.user.sub, session.user.name)
  const runs = await listRuns(user.id)
  return NextResponse.json({ runs })
}

export async function POST(req: Request) {
  const session = await getSession()
  if (!session?.user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const body = await req.json().catch(() => ({}))
  const { goal, urlA, urlB } = body as { goal?: string; urlA?: string; urlB?: string }

  const user = await upsertUser(session.user.sub, session.user.email ?? session.user.sub, session.user.name)
  const run = await createRun(user.id, { goal, urlA, urlB })
  return NextResponse.json(run, { status: 201 })
}
