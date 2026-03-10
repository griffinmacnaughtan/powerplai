'use client'

import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { motion } from 'framer-motion'

interface CalibrationBucket {
  predicted: number
  actual: number
  sampleSize: number
  calibrated: boolean
}

interface CalibrationChartProps {
  data: CalibrationBucket[]
  title?: string
  brierScore?: number
}

export function CalibrationChart({ data, title = 'Model Calibration', brierScore }: CalibrationChartProps) {
  // Format data for the chart
  const chartData = data.map(bucket => ({
    predicted: bucket.predicted * 100,
    actual: bucket.actual * 100,
    size: Math.sqrt(bucket.sampleSize) * 3,
    sampleSize: bucket.sampleSize,
    calibrated: bucket.calibrated,
  }))

  // Perfect calibration line points
  const perfectLine = [
    { predicted: 0, actual: 0 },
    { predicted: 100, actual: 100 },
  ]

  const getBrierInterpretation = (score: number | undefined) => {
    if (score === undefined) return null
    if (score < 0.15) return { text: 'Excellent', color: 'text-green-500' }
    if (score < 0.25) return { text: 'Good', color: 'text-yellow-500' }
    if (score < 0.35) return { text: 'Fair', color: 'text-orange-500' }
    return { text: 'Needs Improvement', color: 'text-red-500' }
  }

  const interpretation = getBrierInterpretation(brierScore)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-surface border border-border rounded-xl p-4 shadow-card"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
        {brierScore !== undefined && (
          <div className="text-right">
            <span className="text-sm text-text-muted">Brier Score: </span>
            <span className={`font-semibold ${interpretation?.color}`}>
              {brierScore.toFixed(3)} ({interpretation?.text})
            </span>
          </div>
        )}
      </div>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              type="number"
              dataKey="predicted"
              name="Predicted %"
              domain={[0, 100]}
              stroke="#9CA3AF"
              fontSize={12}
              tickFormatter={(v: number) => `${v}%`}
              label={{ value: 'Predicted Probability', position: 'bottom', fill: '#9CA3AF', fontSize: 12 }}
            />
            <YAxis
              type="number"
              dataKey="actual"
              name="Actual %"
              domain={[0, 100]}
              stroke="#9CA3AF"
              fontSize={12}
              tickFormatter={(v: number) => `${v}%`}
              label={{ value: 'Actual Rate', angle: -90, position: 'left', fill: '#9CA3AF', fontSize: 12 }}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1F2937',
                border: '1px solid #374151',
                borderRadius: '8px',
              }}
              formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
              labelFormatter={() => ''}
            />
            {/* Perfect calibration line */}
            <ReferenceLine
              segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
              stroke="#10B981"
              strokeDasharray="5 5"
              strokeWidth={2}
            />
            <Scatter
              name="Calibration"
              data={chartData}
              fill="#3B82F6"
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4 flex items-center justify-center gap-4 text-xs text-text-muted">
        <span className="flex items-center gap-2">
          <span className="w-3 h-0.5 bg-green-500 inline-block" style={{ borderStyle: 'dashed' }} />
          Perfect Calibration
        </span>
        <span className="flex items-center gap-2">
          <span className="w-3 h-3 bg-primary rounded-full inline-block" />
          Model Performance
        </span>
      </div>
    </motion.div>
  )
}
