'use client'

import { motion } from 'framer-motion'
import { TrendingUp, TrendingDown, Target, Flame, Snowflake } from 'lucide-react'

interface Prediction {
  playerName: string
  team: string
  opponent: string
  probGoal: number
  probPoint: number
  expectedGoals: number
  expectedPoints: number
  confidence: 'high' | 'medium' | 'low'
  factors: string[]
  isHome: boolean
}

interface PredictionCardProps {
  prediction: Prediction
  rank?: number
}

export function PredictionCard({ prediction, rank }: PredictionCardProps) {
  const getConfidenceColor = (confidence: string) => {
    switch (confidence) {
      case 'high': return 'text-green-500 bg-green-500/10'
      case 'medium': return 'text-yellow-500 bg-yellow-500/10'
      case 'low': return 'text-red-500 bg-red-500/10'
      default: return 'text-gray-500 bg-gray-500/10'
    }
  }

  const getStreakIcon = (factors: string[]) => {
    const hasHotStreak = factors.some(f => f.toLowerCase().includes('hot'))
    const hasColdStreak = factors.some(f => f.toLowerCase().includes('cold'))
    if (hasHotStreak) return <Flame className="w-4 h-4 text-orange-500" />
    if (hasColdStreak) return <Snowflake className="w-4 h-4 text-blue-400" />
    return null
  }

  const goalProbPercent = Math.round(prediction.probGoal * 100)
  const pointProbPercent = Math.round(prediction.probPoint * 100)

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-surface border border-border rounded-xl p-4 shadow-card hover:shadow-soft transition-shadow"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          {rank && (
            <span className="w-8 h-8 rounded-full bg-primary/20 text-primary font-bold flex items-center justify-center text-sm">
              {rank}
            </span>
          )}
          <div>
            <h4 className="font-semibold text-text-primary flex items-center gap-2">
              {prediction.playerName}
              {getStreakIcon(prediction.factors)}
            </h4>
            <p className="text-sm text-text-muted">
              {prediction.team} {prediction.isHome ? 'vs' : '@'} {prediction.opponent}
            </p>
          </div>
        </div>
        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getConfidenceColor(prediction.confidence)}`}>
          {prediction.confidence}
        </span>
      </div>

      {/* Probability bars */}
      <div className="space-y-3 mb-4">
        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-text-secondary">Goal Probability</span>
            <span className="font-medium text-text-primary">{goalProbPercent}%</span>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${goalProbPercent}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
              className="h-full bg-gradient-to-r from-primary to-primary-dark rounded-full"
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-text-secondary">Point Probability</span>
            <span className="font-medium text-text-primary">{pointProbPercent}%</span>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${pointProbPercent}%` }}
              transition={{ duration: 0.5, ease: 'easeOut', delay: 0.1 }}
              className="h-full bg-gradient-to-r from-ice to-ice-dark rounded-full"
            />
          </div>
        </div>
      </div>

      {/* Expected values */}
      <div className="flex gap-4 mb-3">
        <div className="flex items-center gap-2 text-sm">
          <Target className="w-4 h-4 text-primary" />
          <span className="text-text-secondary">xG:</span>
          <span className="font-medium text-text-primary">{prediction.expectedGoals.toFixed(2)}</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-4 h-4 text-ice" />
          <span className="text-text-secondary">xP:</span>
          <span className="font-medium text-text-primary">{prediction.expectedPoints.toFixed(2)}</span>
        </div>
      </div>

      {/* Key factors */}
      {prediction.factors.length > 0 && (
        <div className="pt-3 border-t border-border">
          <div className="flex flex-wrap gap-1.5">
            {prediction.factors.slice(0, 3).map((factor, idx) => (
              <span
                key={idx}
                className="text-xs px-2 py-1 rounded-md bg-gray-800 text-text-secondary"
              >
                {factor}
              </span>
            ))}
          </div>
        </div>
      )}
    </motion.div>
  )
}
