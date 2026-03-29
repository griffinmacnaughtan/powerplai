'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'
import { ModelPerformanceModal } from './ModelPerformanceModal'

interface AccuracySummary {
  nhl: { goal_hit_rate: string; validated: number } | null
}

export function AccuracyBadge() {
  const [data, setData] = useState<AccuracySummary | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api.getAccuracySummary(7).then(res => {
      setData(res as AccuracySummary)
    }).catch(() => {})
  }, [])

  const hasData = data?.nhl && data.nhl.validated > 0
  const rate = data?.nhl?.goal_hit_rate
  const n = data?.nhl?.validated ?? 0

  return (
    <>
      <motion.button
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setOpen(true)}
        title={hasData ? `${rate} accuracy on ${n} picks (7d) — click for details` : 'View model performance'}
        className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface border border-border hover:border-ice/40 text-xs text-text-muted hover:text-ice cursor-pointer select-none transition-colors"
      >
        {hasData ? (
          <>
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            <span className="font-semibold">{rate}</span>
            <span className="text-text-muted">({n} picks)</span>
          </>
        ) : (
          <>
            <TrendingUp className="w-3 h-3" />
            <span>Model Stats</span>
          </>
        )}
      </motion.button>

      <ModelPerformanceModal open={open} onClose={() => setOpen(false)} />
    </>
  )
}
