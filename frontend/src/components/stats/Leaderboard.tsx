'use client'

import { motion } from 'framer-motion'
import { Trophy, Medal, Award } from 'lucide-react'
import clsx from 'clsx'

interface LeaderboardEntry {
  rank: number
  name: string
  team: string
  value: number | string
  subValue?: string
}

interface LeaderboardProps {
  title: string
  entries: LeaderboardEntry[]
  statLabel: string
}

export function Leaderboard({ title, entries, statLabel }: LeaderboardProps) {
  const getRankIcon = (rank: number) => {
    switch (rank) {
      case 1:
        return <Trophy className="w-5 h-5 text-yellow-400" />
      case 2:
        return <Medal className="w-5 h-5 text-gray-300" />
      case 3:
        return <Award className="w-5 h-5 text-amber-600" />
      default:
        return null
    }
  }

  return (
    <div className="bg-surface-elevated border border-border rounded-2xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border">
        <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
      </div>

      {/* Entries */}
      <div className="divide-y divide-border/50">
        {entries.map((entry, i) => (
          <motion.div
            key={entry.rank}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
            className={clsx(
              'flex items-center gap-4 px-6 py-3 hover:bg-surface transition-colors',
              entry.rank <= 3 && 'bg-accent/5'
            )}
          >
            {/* Rank */}
            <div className="w-10 flex items-center justify-center">
              {getRankIcon(entry.rank) || (
                <span className="text-text-muted font-mono">{entry.rank}</span>
              )}
            </div>

            {/* Player info */}
            <div className="flex-1 min-w-0">
              <p className="font-medium text-text-primary truncate">{entry.name}</p>
              <p className="text-sm text-text-muted">{entry.team}</p>
            </div>

            {/* Stat value */}
            <div className="text-right">
              <p className="font-bold text-text-primary">{entry.value}</p>
              {entry.subValue && (
                <p className="text-xs text-text-muted">{entry.subValue}</p>
              )}
            </div>
          </motion.div>
        ))}
      </div>

      {/* Footer */}
      <div className="px-6 py-3 border-t border-border bg-surface/50">
        <p className="text-xs text-text-muted text-center">
          {statLabel} • 2023-24 Season
        </p>
      </div>
    </div>
  )
}
