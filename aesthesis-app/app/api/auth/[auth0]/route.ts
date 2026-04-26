// Auth routes (/api/auth/login, /api/auth/logout, /api/auth/callback)
// are handled by proxy.ts via auth0.middleware — this file should never be reached.
export async function GET() {
  return new Response("Not found", { status: 404 })
}
