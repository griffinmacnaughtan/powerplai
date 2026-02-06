'use client'

import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'

const HOCKEY_PHRASES = [
  "Dropping the puck...",
  "Dropping the gloves...",
  "Checking the boards...",
  "Reviewing the tape...",
  "Pulling the goalie...",
  "Calling up from the AHL...",
  "Forechecking the data...",
  "Backchecking the stats...",
  "Clearing the zone...",
  "Going top shelf...",
  "Sniping the corners...",
  "Dangling defenders...",
  "Cycling the zone...",
  "Screening the goalie...",
  "Killing the penalty...",
  "Winning the faceoff...",
  "Icing the analysis...",
  "Calculating expected goals...",
  "Reviewing power play data...",
  "Scouting the opposition...",
  "Saucing"
]

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
  const [phraseIndex, setPhraseIndex] = useState(0)

  useEffect(() => {
    // Pick a random starting phrase
    setPhraseIndex(Math.floor(Math.random() * HOCKEY_PHRASES.length))

    const interval = setInterval(() => {
      setPhraseIndex((prev) => (prev + 1) % HOCKEY_PHRASES.length)
    }, 2000)

    return () => clearInterval(interval)
  }, [])

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
      <motion.span
        key={phraseIndex}
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -5 }}
        className="text-sm font-medium"
      >
        {HOCKEY_PHRASES[phraseIndex]}
      </motion.span>
    </div>
  )
}
