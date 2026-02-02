'use client'

import { motion } from 'framer-motion'

interface LogoProps {
  size?: 'sm' | 'md' | 'lg'
  animate?: boolean
}

export function Logo({ size = 'md', animate = true }: LogoProps) {
  const sizes = {
    sm: 'w-9 h-9',
    md: 'w-12 h-12',
    lg: 'w-20 h-20',
  }

  const iconSizes = {
    sm: 'text-sm',
    md: 'text-lg',
    lg: 'text-3xl',
  }

  const Component = animate ? motion.div : 'div'

  return (
    <Component
      className={`${sizes[size]} relative`}
      {...(animate && {
        whileHover: { scale: 1.05 },
        whileTap: { scale: 0.95 },
        transition: { type: 'spring', stiffness: 400, damping: 15 },
      })}
    >
      {/* NHL-style shield shape */}
      <div className="absolute inset-0 bg-gradient-to-b from-primary to-primary-dark rounded-lg shadow-nhl"
           style={{ clipPath: 'polygon(0 0, 100% 0, 100% 75%, 50% 100%, 0 75%)' }} />

      {/* Lightning bolt / Power symbol */}
      <div className="absolute inset-0 flex items-center justify-center">
        <svg
          viewBox="0 0 24 24"
          className={`${iconSizes[size]} text-white drop-shadow-lg`}
          fill="currentColor"
          style={{ width: '55%', height: '55%', marginTop: '-5%' }}
        >
          <path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z" />
        </svg>
      </div>

      {/* Accent stripe */}
      <div
        className="absolute left-1/2 -translate-x-1/2 bottom-[18%] w-[60%] h-[3px] bg-accent rounded-full"
      />
    </Component>
  )
}

export function LogoText({ className = '' }: { className?: string }) {
  return (
    <span className={`font-bold tracking-tight ${className}`}>
      <span className="text-primary">Powerpl</span>
      <span className="text-accent">AI</span>
    </span>
  )
}

export function LogoFull({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const textSizes = {
    sm: 'text-lg',
    md: 'text-xl',
    lg: 'text-3xl',
  }

  return (
    <div className="flex items-center gap-2.5">
      <Logo size={size} />
      <LogoText className={textSizes[size]} />
    </div>
  )
}
