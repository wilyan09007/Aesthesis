import { Auth0Client } from "@auth0/nextjs-auth0/server"
import type { SessionData } from "@auth0/nextjs-auth0/types"

export const auth0 = new Auth0Client({
  routes: {
    login: "/api/auth/login",
    logout: "/api/auth/logout",
    callback: "/api/auth/callback",
  },
})

export async function getSession(): Promise<SessionData | null> {
  try {
    return await auth0.getSession()
  } catch {
    return null
  }
}
