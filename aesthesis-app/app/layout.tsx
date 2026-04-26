import type { Metadata } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import "./globals.css"
import Providers from "@/components/Providers"
import { getSession } from "@/lib/auth0"

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] })
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Aesthesis — Neural Demo Analysis",
  description: "AI-driven analysis of any product demo through simulated brain response.",
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()

  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>
        <Providers user={session?.user}>
          {children}
        </Providers>
      </body>
    </html>
  )
}
