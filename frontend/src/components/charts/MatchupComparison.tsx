'use client'

import { motion } from 'framer-motion'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell } from 'recharts'
import { Home, Plane } from 'lucide-react'

interface Team {
  abbrev: string
  name: string
  goalie?: {
    name: string
    savePct: number
  }
  expectedGoals: number
  pace: string
}

interface MatchupComparisonProps {
  homeTeam: Team
  awayTeam: Team
  expectedTotal: number
  paceRating: 'high' | 'average' | 'low'
  startTime?: string
  venue?: string
}

export function MatchupComparison({
  homeTeam,
  awayTeam,
  expectedTotal,
  paceRating,
  startTime,
  venue,
}: MatchupComparisonProps) {
  const getPaceColor = (pace: string) => {
    switch (pace) {
      case 'high': return 'text-green-500 bg-green-500/10'
      case 'low': return 'text-blue-400 bg-blue-400/10'
      default: return 'text-yellow-500 bg-yellow-500/10'
    }
  }

  const chartData = [
    { name: awayTeam.abbrev, goals: awayTeam.expectedGoals, fill: '#EF4444' },
    { name: homeTeam.abbrev, goals: homeTeam.expectedGoals, fill: '#3B82F6' },
  ]

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-surface border border-border rounded-xl overflow-hidden shadow-card"
    >
      {/* Header */}
      <div className="bg-gradient-to-r from-gray-800 to-gray-900 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Plane className="w-4 h-4 text-text-muted" />
          <span className="font-bold text-lg">{awayTeam.abbrev}</span>
        </div>
        <div className="text-center">
          <span className="text-xs text-text-muted">vs</span>
          {startTime && (
            <p className="text-xs text-text-muted">{startTime}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="font-bold text-lg">{homeTeam.abbrev}</span>
          <Home className="w-4 h-4 text-text-muted" />
        </div>
      </div>

      {/* Main content */}
      <div className="p-4">
        {/* Expected goals chart */}
        <div className="mb-4">
          <h4 className="text-sm text-text-muted mb-2 text-center">Expected Goals</h4>
          <div className="h-24">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" barSize={24}>
                <XAxis type="number" domain={[0, 5]} hide />
                <YAxis type="category" dataKey="name" axisLine={false} tickLine={false} width={40} />
                <Bar dataKey="goals" radius={[0, 4, 4, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex justify-between text-sm font-medium">
            <span className="text-red-500">{awayTeam.expectedGoals.toFixed(2)} xG</span>
            <span className="text-primary">{homeTeam.expectedGoals.toFixed(2)} xG</span>
          </div>
        </div>

        {/* Goalie matchups */}
        <div className="grid grid-cols-2 gap-4 mb-4 pt-4 border-t border-border">
          <div className="text-center">
            {awayTeam.goalie ? (
              <>
                <p className="text-sm font-medium text-text-primary">{awayTeam.goalie.name}</p>
                <p className="text-xs text-text-muted">
                  SV%: {(awayTeam.goalie.savePct * 100).toFixed(1)}%
                </p>
              </>
            ) : (
              <p className="text-sm text-text-muted">Goalie TBD</p>
            )}
          </div>
          <div className="text-center">
            {homeTeam.goalie ? (
              <>
                <p className="text-sm font-medium text-text-primary">{homeTeam.goalie.name}</p>
                <p className="text-xs text-text-muted">
                  SV%: {(homeTeam.goalie.savePct * 100).toFixed(1)}%
                </p>
              </>
            ) : (
              <p className="text-sm text-text-muted">Goalie TBD</p>
            )}
          </div>
        </div>

        {/* Game context */}
        <div className="flex items-center justify-between pt-4 border-t border-border">
          <div>
            <span className="text-xs text-text-muted">Total Goals</span>
            <p className="font-semibold text-text-primary">{expectedTotal.toFixed(1)} expected</p>
          </div>
          <span className={`px-3 py-1 rounded-full text-sm font-medium ${getPaceColor(paceRating)}`}>
            {paceRating.charAt(0).toUpperCase() + paceRating.slice(1)} Pace
          </span>
        </div>

        {venue && (
          <p className="text-xs text-text-muted text-center mt-3">{venue}</p>
        )}
      </div>
    </motion.div>
  )
}
