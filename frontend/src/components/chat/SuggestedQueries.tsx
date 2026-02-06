'use client'

import { motion } from 'framer-motion'
import { Target, DollarSign, TrendingUp, Flame, Scale, Trophy } from 'lucide-react'

interface SuggestedQueriesProps {
  onSelect: (query: string) => void
}

const suggestions = [
  {
    icon: Flame,
    query: 'Who will score tonight? Give me your top 3 picks with explanations',
    label: 'Tonight\'s Scorers',
    color: 'text-accent',
    bgColor: 'bg-accent-muted',
  },
  {
    icon: DollarSign,
    query: 'Who are the best value players in the league right now?',
    label: 'Best Value',
    color: 'text-green-600',
    bgColor: 'bg-green-50',
  },
  {
    icon: Scale,
    query: 'Will the Oilers vs Leafs game go over or under 6.5 goals?',
    label: 'Over/Under',
    color: 'text-primary',
    bgColor: 'bg-primary-50',
  },
  {
    icon: Target,
    query: 'Who leads the league in expected goals this season?',
    label: 'xG Leaders',
    color: 'text-ice-dark',
    bgColor: 'bg-ice/10',
  },
  {
    icon: TrendingUp,
    query: 'Who is better value, Connor Bedard or Macklin Celebrini?',
    label: 'Value Compare',
    color: 'text-purple-600',
    bgColor: 'bg-purple-50',
  },
  {
    icon: Trophy,
    query: 'Which players are outperforming their expected goals?',
    label: 'Overperformers',
    color: 'text-amber-600',
    bgColor: 'bg-amber-50',
  },
]

export function SuggestedQueries({ onSelect }: SuggestedQueriesProps) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
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
