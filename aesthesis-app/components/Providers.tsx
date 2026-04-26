"use client"

import { Auth0Provider } from "@auth0/nextjs-auth0/client"
import type { User } from "@auth0/nextjs-auth0/types"

export default function Providers({
  user,
  children,
}: {
  user?: User
  children: React.ReactNode
}) {
  return <Auth0Provider user={user}>{children}</Auth0Provider>
}
