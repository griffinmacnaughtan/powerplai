'use client'

import { motion } from 'framer-motion'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import clsx from 'clsx'

interface StatCardProps {
  label: string
  value: string | number
  subValue?: string
  trend?: 'up' | 'down' | 'neutral'
  trendValue?: string
  icon?: React.ReactNode
  color?: 'default' | 'accent' | 'success' | 'warning'
}

export function StatCard({
  label,
  value,
  subValue,
  trend,
  trendValue,
  icon,
  color = 'default',
}: StatCardProps) {
  const colors = {
    default: 'from-surface-elevated to-surface',
    accent: 'from-accent/20 to-accent/5',
    success: 'from-success/20 to-success/5',
    warning: 'from-warning/20 to-warning/5',
  }

  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      whileHover={{ y: -2 }}
      className={clsx(
        'relative p-4 rounded-xl border border-border overflow-hidden',
        'bg-gradient-to-br',
        colors[color]
      )}
    >
      {/* Background accent */}
      {icon && (
        <div className="absolute top-2 right-2 text-text-muted/20">
          {icon}
        </div>
      )}

      <div className="relative">
        <p className="text-xs font-medium text-text-muted uppercase tracking-wider mb-1">
          {label}
        </p>

        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-text-primary">
            {value}
          </span>
          {subValue && (
            <span className="text-sm text-text-secondary">{subValue}</span>
          )}
        </div>

        {trend && trendValue && (
          <div
            className={clsx(
              'flex items-center gap-1 mt-2 text-xs font-medium',
              trend === 'up' && 'text-success',
              trend === 'down' && 'text-error',
              trend === 'neutral' && 'text-text-muted'
            )}
          >
            <TrendIcon className="w-3 h-3" />
            <span>{trendValue}</span>
          </div>
        )}
      </div>
    </motion.div>
  )
}

interface PlayerStatGridProps {
  stats: {
    gamesPlayed: number
    goals: number
    assists: number
    points: number
    xg?: number | null
    corsi?: number | null
  }
}

export function PlayerStatGrid({ stats }: PlayerStatGridProps) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <StatCard label="GP" value={stats.gamesPlayed} />
      <StatCard label="Goals" value={stats.goals} color="accent" />
      <StatCard label="Assists" value={stats.assists} />
      <StatCard label="Points" value={stats.points} color="accent" />
      {stats.xg !== null && stats.xg !== undefined && (
        <StatCard
          label="xG"
          value={stats.xg.toFixed(1)}
          subValue="expected"
        />
      )}
      {stats.corsi !== null && stats.corsi !== undefined && (
        <StatCard
          label="CF%"
          value={`${stats.corsi.toFixed(1)}%`}
          trend={stats.corsi > 50 ? 'up' : stats.corsi < 50 ? 'down' : 'neutral'}
        />
      )}
    </div>
  )
}
