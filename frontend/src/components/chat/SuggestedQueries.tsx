'use client'

import { motion } from 'framer-motion'
import { TrendingUp, Users, Target, Trophy } from 'lucide-react'

interface SuggestedQueriesProps {
  onSelect: (query: string) => void
}

const suggestions = [
  {
    icon: Target,
    query: 'Who leads the league in expected goals this season?',
    label: 'xG Leaders',
    color: 'text-ice-dark',
    bgColor: 'bg-ice/10',
  },
  {
    icon: TrendingUp,
    query: 'Compare Connor McDavid vs Leon Draisaitl this season',
    label: 'Player Compare',
    color: 'text-primary',
    bgColor: 'bg-primary-50',
  },
  {
    icon: Users,
    query: 'Top 3 players on each team by goals this season',
    label: 'Team Breakdown',
    color: 'text-accent',
    bgColor: 'bg-accent-muted',
  },
  {
    icon: Trophy,
    query: 'Toronto Maple Leafs players ranked by points this season',
    label: 'Team Stats',
    color: 'text-primary',
    bgColor: 'bg-primary-50',
  },
]

export function SuggestedQueries({ onSelect }: SuggestedQueriesProps) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {suggestions.map((suggestion, i) => (
        <motion.button
          key={suggestion.query}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 + i * 0.1 }}
          onClick={() => onSelect(suggestion.query)}
          className="group flex items-center gap-3 p-4 rounded-xl bg-surface border border-border hover:border-primary/40 hover:shadow-soft transition-all duration-300 text-left card-hover"
        >
          <div className={`flex-shrink-0 w-11 h-11 rounded-lg ${suggestion.bgColor} flex items-center justify-center group-hover:scale-105 transition-transform`}>
            <suggestion.icon className={`w-5 h-5 ${suggestion.color}`} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-text-primary group-hover:text-primary transition-colors">
              {suggestion.label}
            </p>
            <p className="text-xs text-text-muted truncate mt-0.5">
              {suggestion.query}
            </p>
          </div>
        </motion.button>
      ))}
    </div>
  )
}
