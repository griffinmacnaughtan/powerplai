'use client'

import { motion } from 'framer-motion'
import { Target, DollarSign, TrendingDown, Flame, Activity, Trophy } from 'lucide-react'

interface SuggestedQueriesProps {
  onSelect: (query: string) => void
}

const suggestions = [
  {
    icon: Flame,
    query: "Give me tonight's top 5 scoring picks with market odds — where does the model see the biggest edge?",
    label: "Top Picks + Odds",
    color: 'text-accent dark:text-accent-light',
    bgColor: 'bg-accent-muted dark:bg-accent/15',
  },
  {
    icon: DollarSign,
    query: "What are the best betting edges tonight? Grade them and explain why.",
    label: 'Best Bets Tonight',
    color: 'text-green-600 dark:text-green-400',
    bgColor: 'bg-green-50 dark:bg-green-950/60',
  },
  {
    icon: TrendingDown,
    query: "Which star players are way overdue for a regression? Their goals are way ahead of their xG.",
    label: 'Bust Risk',
    color: 'text-amber-600 dark:text-amber-400',
    bgColor: 'bg-amber-50 dark:bg-amber-950/60',
  },
  {
    icon: Target,
    query: 'Who are the top 10 players in expected goals this season and how do they compare to their actual goal totals?',
    label: 'xG vs Actual Goals',
    color: 'text-ice-dark dark:text-ice',
    bgColor: 'bg-ice/10 dark:bg-ice/10',
  },
  {
    icon: Activity,
    query: 'Who are the five hottest forwards in the league right now based on the last 10 games?',
    label: 'Hottest Players',
    color: 'text-purple-600 dark:text-purple-400',
    bgColor: 'bg-purple-50 dark:bg-purple-950/60',
  },
  {
    icon: Trophy,
    query: 'I need goals in my fantasy lineup — which available forwards have the best goal-scoring probability this week?',
    label: 'Fantasy: Need Goals',
    color: 'text-primary dark:text-ice',
    bgColor: 'bg-primary-50 dark:bg-primary/20',
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
          className="group flex items-center gap-3 p-4 rounded-xl bg-surface dark:bg-surface/80 border border-border dark:border-border/60 hover:border-primary/40 dark:hover:border-ice/35 hover:shadow-soft dark:hover:shadow-[0_4px_20px_-4px_rgba(91,192,235,0.12)] transition-all duration-300 text-left card-hover"
        >
          <div className={`flex-shrink-0 w-11 h-11 rounded-lg ${suggestion.bgColor} flex items-center justify-center group-hover:scale-105 transition-transform`}>
            <suggestion.icon className={`w-5 h-5 ${suggestion.color}`} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-text-primary group-hover:text-primary dark:group-hover:text-ice transition-colors">
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
