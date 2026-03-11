'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'

// Use NEXT_PUBLIC_BASE_PATH so the GIF resolves correctly on GitHub Pages
// (where the site lives at /powerplai/) as well as on Railway (no basePath)
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || ''

interface LogoProps {
  size?: 'sm' | 'md' | 'lg'
  animate?: boolean
  link?: boolean
}

export function Logo({ size = 'md', animate = true, link = true }: LogoProps) {
  const sizes = {
    sm: { width: 36, height: 36 },
    md: { width: 48, height: 48 },
    lg: { width: 80, height: 80 },
  }

  const Component = animate ? motion.div : 'div'

  const logoImage = (
    <Component
      className="relative"
      style={{ width: sizes[size].width, height: sizes[size].height }}
      {...(animate && {
        whileHover: { scale: 1.05 },
        whileTap: { scale: 0.95 },
        transition: { type: 'spring', stiffness: 400, damping: 15 },
      })}
    >
      {/* Using a plain img tag so basePath is applied correctly for both
          GitHub Pages (static export) and Railway (Docker) deployments */}
      <img
        src={`${BASE_PATH}/logo.gif`}
        alt="PowerplAI Logo"
        width={sizes[size].width}
        height={sizes[size].height}
        className="rounded-lg"
      />
    </Component>
  )

  if (link) {
    return (
      <Link href="/" className="cursor-pointer">
        {logoImage}
      </Link>
    )
  }

  return logoImage
}

export function LogoText({ className = '' }: { className?: string }) {
  return (
    <span className={`font-bold tracking-tight ${className}`}>
      <span className="text-primary dark:text-ice">Powerpl</span>
      <span className="text-ice-dark dark:text-ice-light">AI</span>
    </span>
  )
}

export function LogoFull({ size = 'md', link = true }: { size?: 'sm' | 'md' | 'lg'; link?: boolean }) {
  const textSizes = {
    sm: 'text-lg',
    md: 'text-xl',
    lg: 'text-3xl',
  }

  const content = (
    <div className="flex items-center gap-2.5">
      <Logo size={size} animate={true} link={false} />
      <LogoText className={textSizes[size]} />
    </div>
  )

  if (link) {
    return (
      <Link href="/" className="cursor-pointer hover:opacity-90 transition-opacity">
        {content}
      </Link>
    )
  }

  return content
}
