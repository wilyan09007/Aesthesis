import { NextRequest, NextResponse } from "next/server"
import { auth0 } from "@/lib/auth0"

export async function proxy(req: NextRequest) {
  try {
    return await auth0.middleware(req)
  } catch {
    return NextResponse.next()
  }
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico).*)"],
}
