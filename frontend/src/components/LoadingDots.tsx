'use client'

import { motion } from 'framer-motion'

export function LoadingDots() {
  return (
    <div className="flex items-center gap-1">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="w-2 h-2 bg-primary rounded-full"
          animate={{
            y: [0, -6, 0],
            opacity: [0.5, 1, 0.5],
          }}
          transition={{
            duration: 0.8,
            repeat: Infinity,
            delay: i * 0.15,
            ease: 'easeInOut',
          }}
        />
      ))}
    </div>
  )
}

export function PuckLoader() {
  return (
    <div className="flex items-center justify-center">
      <motion.div
        className="w-10 h-10 rounded-full bg-gradient-to-br from-primary to-primary-dark border-4 border-ice/30"
        animate={{
          rotate: 360,
          scale: [1, 0.95, 1],
        }}
        transition={{
          rotate: {
            duration: 1.2,
            repeat: Infinity,
            ease: 'linear',
          },
          scale: {
            duration: 0.6,
            repeat: Infinity,
            ease: 'easeInOut',
          },
        }}
        style={{
          boxShadow: '0 0 20px rgba(4, 30, 66, 0.3)',
        }}
      />
    </div>
  )
}

export function TypingIndicator() {
  return (
    <div className="flex items-center gap-3 text-text-secondary">
      <div className="flex items-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="w-2 h-2 bg-primary rounded-full"
            animate={{
              scale: [1, 1.4, 1],
              opacity: [0.4, 1, 0.4],
            }}
            transition={{
              duration: 1,
              repeat: Infinity,
              delay: i * 0.2,
            }}
          />
        ))}
      </div>
      <span className="text-sm font-medium">Analyzing stats...</span>
    </div>
  )
}
