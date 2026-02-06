import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
})

const mono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
})

export const metadata: Metadata = {
  title: 'PowerplAI | AI Hockey Analytics & Fantasy',
  description: 'Your AI-powered hockey analyst. Get insights on NHL stats, player comparisons, predictions, and fantasy hockey advice.',
  keywords: ['hockey', 'NHL', 'analytics', 'AI', 'statistics', 'expected goals', 'xG', 'fantasy hockey', 'predictions', 'PowerplAI'],
  icons: {
    icon: '/logo.gif',
    apple: '/logo.gif',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="bg-background text-text-primary antialiased min-h-screen">
        {/* Ice rink inspired background */}
        <div className="fixed inset-0 ice-pattern pointer-events-none" />

        {/* Subtle decorative elements */}
        <div className="fixed inset-0 overflow-hidden pointer-events-none">
          {/* Top-left blue glow */}
          <div className="absolute -top-40 -left-40 w-96 h-96 bg-primary/5 rounded-full blur-3xl" />
          {/* Top-right ice glow */}
          <div className="absolute -top-20 right-20 w-72 h-72 bg-ice/10 rounded-full blur-3xl" />
          {/* Bottom accent glow */}
          <div className="absolute -bottom-40 left-1/3 w-80 h-80 bg-accent/5 rounded-full blur-3xl" />
        </div>

        {/* Main content */}
        <div className="relative z-10">
          {children}
        </div>
      </body>
    </html>
  )
}
