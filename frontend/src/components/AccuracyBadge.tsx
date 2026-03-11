'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { api } from '@/lib/api'

interface AccuracySummary {
  nhl: { goal_hit_rate: string; validated: number } | null
}

export function AccuracyBadge() {
  const [data, setData] = useState<AccuracySummary | null>(null)

  useEffect(() => {
    api.getAccuracySummary(7).then(res => {
      if (res?.nhl && res.nhl.validated >= 10) {
        setData(res as AccuracySummary)
      }
    }).catch(() => {})
  }, [])

  if (!data?.nhl) return null

  const rate = data.nhl.goal_hit_rate  // e.g. "61.2%"
  const n = data.nhl.validated

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      title={`Based on ${n} validated predictions in the last 7 days`}
      className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface border border-border text-xs text-text-muted cursor-default select-none"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
      <span>{rate} accurate (7d)</span>
    </motion.div>
  )
}
