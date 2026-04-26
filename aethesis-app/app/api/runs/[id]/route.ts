import { NextResponse } from "next/server"
import { getSession } from "@/lib/auth0"
import { upsertUser, getRunForUser } from "@/lib/db/runs"

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession()
  if (!session?.user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const { id } = await params
  const user = await upsertUser(session.user.sub, session.user.email ?? session.user.sub, session.user.name)
  const run = await getRunForUser(id, user.id)
  if (!run) return NextResponse.json({ error: "Not found" }, { status: 404 })

  return NextResponse.json({ run })
}
