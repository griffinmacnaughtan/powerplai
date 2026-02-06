'use client'

import Link from 'next/link'
import Image from 'next/image'
import { motion } from 'framer-motion'

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
      <Image
        src="/logo.gif"
        alt="PowerplAI Logo"
        width={sizes[size].width}
        height={sizes[size].height}
        className="rounded-lg"
        unoptimized
        priority
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
      <span className="text-primary">Powerpl</span>
      <span className="text-ice-dark">AI</span>
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
