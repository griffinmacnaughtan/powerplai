'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { api } from '@/lib/api'

type RecentEntry = {
  date: string
  name: string
  result: string | null
}

type ParlaySummary = {
  parlay_name: string
  wins: number
  losses: number
  win_rate: string
  avg_legs_hit_pct: string
  streak: Array<{ date: string; result: string | null }>
}

const RESULT_STYLES: Record<string, { label: string; bg: string; text: string }> = {
  win:  { label: 'W', bg: 'bg-green-500',  text: 'text-white' },
  loss: { label: 'L', bg: 'bg-red-500',    text: 'text-white' },
  push: { label: 'P', bg: 'bg-amber-400',  text: 'text-white' },
}

const PENDING = { label: '·', bg: 'bg-border', text: 'text-text-muted' }

function ResultDot({ result }: { result: string | null }) {
  const style = result ? (RESULT_STYLES[result] ?? PENDING) : PENDING
  return (
    <span
      title={result ?? 'pending'}
      className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${style.bg} ${style.text}`}
    >
      {style.label}
    </span>
  )
}

export function ParlayTracker() {
  const [parlays, setParlays] = useState<ParlaySummary[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getParlayRecord(30).then(data => {
      if (!data.by_type.length) { setLoading(false); return }

      // Group recent results by parlay name, most recent first
      const recentByName: Record<string, RecentEntry[]> = {}
      for (const r of data.recent) {
        if (!recentByName[r.name]) recentByName[r.name] = []
        recentByName[r.name].push(r)
      }

      const summaries: ParlaySummary[] = data.by_type.map(p => ({
        parlay_name: p.parlay_name,
        wins: p.wins,
        losses: p.losses,
        win_rate: p.win_rate,
        avg_legs_hit_pct: p.avg_legs_hit_pct,
        streak: (recentByName[p.parlay_name] ?? []).slice(0, 7).reverse(),
      }))

      setParlays(summaries)
      setLoading(false)
    })
  }, [])

  if (loading || !parlays.length) return null

  return (
    <motion.div
      initial={{ y: 20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.38 }}
      className="w-full max-w-sm"
    >
      <div className="rounded-2xl border border-border bg-surface shadow-card px-5 py-4">
        <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-3">
          Model Parlay Record (30 days)
        </p>
        <div className="space-y-3">
          {parlays.map(p => (
            <div key={p.parlay_name} className="flex items-center gap-3">
              <div className="w-24 flex-shrink-0">
                <p className="text-xs font-semibold text-text-primary truncate">{p.parlay_name}</p>
                <p className="text-xs text-text-muted">{p.wins}W / {p.losses}L</p>
              </div>
              <div className="flex items-center gap-1 flex-1">
                {p.streak.map((s, i) => (
                  <ResultDot key={i} result={s.result} />
                ))}
                {p.streak.length === 0 && (
                  <span className="text-xs text-text-muted italic">No history yet</span>
                )}
              </div>
              <span className="text-xs font-semibold text-text-secondary flex-shrink-0">
                {p.win_rate}
              </span>
            </div>
          ))}
        </div>
      </div>
    </motion.div>
  )
}
