import { NextResponse } from "next/server"
import { getSession } from "@/lib/auth0"
import { upsertUser, compareRuns } from "@/lib/db/runs"

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession()
  if (!session?.user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const { id } = await params
  const { searchParams } = new URL(req.url)
  const withId = searchParams.get("with")
  if (!withId) return NextResponse.json({ error: "Missing ?with= param" }, { status: 400 })

  const user = await upsertUser(session.user.sub, session.user.email ?? session.user.sub, session.user.name)
  const result = await compareRuns(id, withId, user.id)
  if (!result) return NextResponse.json({ error: "One or both runs missing summary" }, { status: 404 })

  return NextResponse.json(result)
}
