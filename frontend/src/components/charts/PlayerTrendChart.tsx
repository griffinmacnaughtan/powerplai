'use client'

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { motion } from 'framer-motion'

interface GameLog {
  date: string
  goals: number
  assists: number
  points: number
  shots: number
}

interface PlayerTrendChartProps {
  data: GameLog[]
  playerName: string
  metric?: 'points' | 'goals' | 'shots'
}

export function PlayerTrendChart({ data, playerName, metric = 'points' }: PlayerTrendChartProps) {
  // Calculate rolling average
  const dataWithAvg = data.map((game, idx) => {
    const window = data.slice(Math.max(0, idx - 4), idx + 1)
    const avg = window.reduce((sum, g) => sum + g[metric], 0) / window.length
    return {
      ...game,
      rollingAvg: Number(avg.toFixed(2)),
    }
  })

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-surface border border-border rounded-xl p-4 shadow-card"
    >
      <h3 className="text-lg font-semibold mb-4 text-text-primary">
        {playerName} - {metric.charAt(0).toUpperCase() + metric.slice(1)} Trend
      </h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={dataWithAvg} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="date"
              stroke="#9CA3AF"
              fontSize={12}
              tickFormatter={(value: string) => new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            />
            <YAxis stroke="#9CA3AF" fontSize={12} />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1F2937',
                border: '1px solid #374151',
                borderRadius: '8px',
              }}
              labelStyle={{ color: '#F9FAFB' }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey={metric}
              stroke="#3B82F6"
              strokeWidth={2}
              dot={{ fill: '#3B82F6', strokeWidth: 2, r: 4 }}
              name={metric.charAt(0).toUpperCase() + metric.slice(1)}
            />
            <Line
              type="monotone"
              dataKey="rollingAvg"
              stroke="#10B981"
              strokeWidth={2}
              strokeDasharray="5 5"
              dot={false}
              name="5-Game Avg"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  )
}
