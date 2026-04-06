'use client'

import { useEffect, useState, useMemo, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Target, TrendingUp, CheckCircle2, XCircle, Clock, ChevronDown, ChevronUp, Zap, Award, BarChart3 } from 'lucide-react'
import { api } from '@/lib/api'

type Pick = {
  game_date: string
  game_type: string
  player_name: string
  team: string
  opponent: string
  is_home: boolean
  prob_goal: number
  prob_point: number
  confidence: string
  confidence_score: number
  actual_goals: number | null
  actual_assists: number | null
  actual_points: number | null
  goal_hit: boolean | null
  point_hit: boolean | null
  validated: boolean
}

type ParlayLeg = {
  leg_type: string
  player_name: string | null
  team: string
  opponent: string | null
  probability: number
  hit: boolean | null
}

type DailyParlay = {
  game_date: string
  name: string
  legs: ParlayLeg[]
  combined_prob: number
  result: string
  legs_hit: number | null
  legs_total: number | null
}

type Summary = {
  total: number
  validated: number
  goal_hits: number
  point_hits: number
  goal_hit_rate: number | null
  point_hit_rate: number | null
}

type CalibrationData = {
  goal_calibration: Array<{ predicted: number; actual: number; sample_size: number; calibrated: boolean }>
  brier_score: number
  interpretation: string
} | null

interface Props {
  open: boolean
  onClose: () => void
}

type TabId = 'picks' | 'stats' | 'calibration'

/* ─── Small reusable pieces ─── */

function StatCard({ label, value, sub, icon: Icon, color }: {
  label: string; value: string; sub?: string; icon: React.ElementType; color: string
}) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-xl bg-surface border border-border">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${color}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div>
        <p className="text-lg font-bold text-text-primary">{value}</p>
        <p className="text-xs text-text-muted">{label}</p>
        {sub && <p className="text-xs text-text-muted">{sub}</p>}
      </div>
    </div>
  )
}

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const styles: Record<string, string> = {
    high: 'bg-green-500/15 text-green-400 border-green-500/30',
    medium: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    low: 'bg-red-500/15 text-red-400 border-red-500/30',
  }
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${styles[confidence] ?? styles.medium}`}>
      {confidence}
    </span>
  )
}

function ResultIcon({ hit }: { hit: boolean | null }) {
  if (hit === null) return <Clock className="w-4 h-4 text-text-muted" />
  return hit
    ? <CheckCircle2 className="w-4 h-4 text-green-400" />
    : <XCircle className="w-4 h-4 text-red-400" />
}

function CalibrationBar({ predicted, actual, sampleSize }: { predicted: number; actual: number; sampleSize: number }) {
  const predPct = Math.round(predicted * 100)
  const actPct = Math.round(actual * 100)
  const diff = actPct - predPct
  const isGood = Math.abs(diff) <= 5

  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-12 text-right text-text-muted font-mono">{predPct}%</span>
      <div className="flex-1 h-6 bg-surface-hover rounded-md relative overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 bg-ice/20 rounded-md"
          style={{ width: `${Math.min(predPct, 100)}%` }}
        />
        <div
          className={`absolute inset-y-0 left-0 rounded-md ${isGood ? 'bg-green-500/40' : 'bg-amber-500/40'}`}
          style={{ width: `${Math.min(actPct, 100)}%` }}
        />
        <div className="absolute inset-0 flex items-center justify-end pr-2">
          <span className={`font-semibold ${isGood ? 'text-green-400' : 'text-amber-400'}`}>
            {actPct}% actual
          </span>
        </div>
      </div>
      <span className="w-10 text-text-muted text-right">n={sampleSize}</span>
    </div>
  )
}

function ParlayResultBadge({ result }: { result: string }) {
  const styles: Record<string, string> = {
    win: 'bg-green-500/15 text-green-400 border-green-500/30',
    loss: 'bg-red-500/15 text-red-400 border-red-500/30',
    push: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    pending: 'bg-surface text-text-muted border-border',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-medium ${styles[result] ?? styles.pending}`}>
      {result}
    </span>
  )
}

function LegTypeLabel({ type }: { type: string }) {
  const labels: Record<string, { text: string; color: string }> = {
    moneyline: { text: 'ML', color: 'text-blue-400 bg-blue-500/15 border-blue-500/30' },
    goal_scorer: { text: 'G', color: 'text-red-400 bg-red-500/15 border-red-500/30' },
    assist: { text: 'A', color: 'text-purple-400 bg-purple-500/15 border-purple-500/30' },
    point: { text: 'P', color: 'text-amber-400 bg-amber-500/15 border-amber-500/30' },
  }
  const l = labels[type] ?? { text: type[0]?.toUpperCase() ?? '?', color: 'text-text-muted bg-surface border-border' }
  return (
    <span className={`text-[10px] w-5 h-5 rounded flex items-center justify-center border font-bold ${l.color}`}>
      {l.text}
    </span>
  )
}

/* ─── Main modal ─── */

export function ModelPerformanceModal({ open, onClose }: Props) {
  const [picks, setPicks] = useState<Pick[]>([])
  const [parlays, setParlays] = useState<DailyParlay[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [calibration, setCalibration] = useState<CalibrationData>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<TabId>('picks')
  const [expandedDate, setExpandedDate] = useState<string | null>(null)
  const [days, setDays] = useState(30)
  const [mounted, setMounted] = useState(false)

  // Ensure we only portal after hydration
  useEffect(() => { setMounted(true) }, [])

  useEffect(() => {
    if (!open) return
    setLoading(true)

    Promise.all([
      api.getPicksHistory(days),
      api.getCalibrationChart(),
    ]).then(([picksData, calData]) => {
      setPicks(picksData.picks)
      setParlays(picksData.parlays ?? [])
      setSummary(picksData.summary)
      setCalibration(calData)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [open, days])

  // Lock body scroll when open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
      return () => { document.body.style.overflow = '' }
    }
  }, [open])

  // Escape key
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    if (open) {
      document.addEventListener('keydown', handleKeyDown)
      return () => document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open, handleKeyDown])

  // Group picks by date
  const picksByDate = useMemo(() => {
    const grouped: Record<string, Pick[]> = {}
    for (const p of picks) {
      const d = p.game_date
      if (!grouped[d]) grouped[d] = []
      grouped[d].push(p)
    }
    return Object.entries(grouped).sort(([a], [b]) => b.localeCompare(a))
  }, [picks])

  // Group parlays by date
  const parlaysByDate = useMemo(() => {
    const grouped: Record<string, DailyParlay[]> = {}
    for (const p of parlays) {
      if (!grouped[p.game_date]) grouped[p.game_date] = []
      grouped[p.game_date].push(p)
    }
    return grouped
  }, [parlays])

  // All dates (union of picks and parlays)
  const allDates = useMemo(() => {
    const dates = new Set<string>()
    for (const [d] of picksByDate) dates.add(d)
    for (const d of Object.keys(parlaysByDate)) dates.add(d)
    return Array.from(dates).sort((a, b) => b.localeCompare(a))
  }, [picksByDate, parlaysByDate])

  // Date-level stats (from curated top-3 picks)
  const dateStats = useMemo(() => {
    const stats: Record<string, { total: number; hits: number; validated: number }> = {}
    for (const p of picks) {
      if (!stats[p.game_date]) stats[p.game_date] = { total: 0, hits: 0, validated: 0 }
      stats[p.game_date].total++
      if (p.validated) {
        stats[p.game_date].validated++
        if (p.goal_hit) stats[p.game_date].hits++
      }
    }
    return stats
  }, [picks])

  const tabs: { id: TabId; label: string; icon: React.ElementType }[] = [
    { id: 'picks', label: 'Daily Picks', icon: Target },
    { id: 'stats', label: 'Performance', icon: BarChart3 },
    { id: 'calibration', label: 'Calibration', icon: TrendingUp },
  ]

  if (!mounted) return null

  const modalContent = (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="modal-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm"
            style={{ zIndex: 9998 }}
            onClick={onClose}
          />

          {/* Modal */}
          <motion.div
            key="modal-container"
            initial={{ opacity: 0, scale: 0.95, y: 30 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 30 }}
            transition={{ type: 'spring', damping: 30, stiffness: 350 }}
            className="fixed inset-0 flex items-center justify-center p-4 pointer-events-none"
            style={{ zIndex: 9999 }}
          >
            <div
              className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-2xl bg-background border border-border shadow-2xl overflow-hidden pointer-events-auto"
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex-shrink-0 flex items-center justify-between px-5 py-4 border-b border-border bg-surface/50">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-ice/20 to-primary/20 flex items-center justify-center">
                    <Target className="w-5 h-5 text-ice" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold text-text-primary">Model Performance</h2>
                    <p className="text-xs text-text-muted">Top picks &amp; parlay tracking</p>
                  </div>
                </div>
                <button
                  onClick={onClose}
                  className="p-2 rounded-lg hover:bg-surface-hover text-text-muted hover:text-text-primary transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Tabs */}
              <div className="flex-shrink-0 flex items-center gap-1 px-5 py-2 border-b border-border bg-surface/30">
                {tabs.map(t => (
                  <button
                    key={t.id}
                    onClick={() => setTab(t.id)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      tab === t.id
                        ? 'bg-ice/15 text-ice border border-ice/30'
                        : 'text-text-muted hover:text-text-primary hover:bg-surface-hover border border-transparent'
                    }`}
                  >
                    <t.icon className="w-3.5 h-3.5" />
                    {t.label}
                  </button>
                ))}
                <div className="ml-auto">
                  <select
                    value={days}
                    onChange={e => setDays(Number(e.target.value))}
                    className="text-xs bg-surface border border-border rounded-lg px-2 py-1 text-text-muted cursor-pointer"
                  >
                    <option value={7}>7 days</option>
                    <option value={14}>14 days</option>
                    <option value={30}>30 days</option>
                    <option value={90}>90 days</option>
                  </select>
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 min-h-0 overflow-y-auto p-5">
                {loading ? (
                  <div className="flex items-center justify-center py-16">
                    <div className="flex flex-col items-center gap-3">
                      <div className="w-8 h-8 border-2 border-ice/30 border-t-ice rounded-full animate-spin" />
                      <p className="text-sm text-text-muted">Loading performance data...</p>
                    </div>
                  </div>
                ) : tab === 'picks' ? (
                  <PicksTab
                    allDates={allDates}
                    picksByDate={picksByDate}
                    parlaysByDate={parlaysByDate}
                    dateStats={dateStats}
                    expandedDate={expandedDate}
                    setExpandedDate={setExpandedDate}
                  />
                ) : tab === 'stats' ? (
                  <StatsTab summary={summary} calibration={calibration} />
                ) : (
                  <CalibrationTab calibration={calibration} />
                )}
              </div>

              {/* Footer */}
              {summary && summary.validated > 0 && (
                <div className="flex-shrink-0 px-5 py-3 border-t border-border bg-surface/30 flex items-center justify-between text-xs text-text-muted">
                  <span>{summary.validated} validated from top picks</span>
                  <span className="flex items-center gap-1">
                    <Zap className="w-3 h-3 text-ice" />
                    PowerplAI prediction engine
                  </span>
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )

  return createPortal(modalContent, document.body)
}

/* ─── Tab: Daily Picks ─── */

function PicksTab({ allDates, picksByDate, parlaysByDate, dateStats, expandedDate, setExpandedDate }: {
  allDates: string[]
  picksByDate: [string, Pick[]][]
  parlaysByDate: Record<string, DailyParlay[]>
  dateStats: Record<string, { total: number; hits: number; validated: number }>
  expandedDate: string | null
  setExpandedDate: (d: string | null) => void
}) {
  if (allDates.length === 0) {
    return (
      <div className="text-center py-12">
        <Target className="w-12 h-12 text-text-muted mx-auto mb-3 opacity-40" />
        <p className="text-sm text-text-muted">No picks recorded yet</p>
        <p className="text-xs text-text-muted mt-1">Predictions are logged before games and validated after</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {allDates.map((dateStr) => {
        const datePicks = picksByDate.find(([d]) => d === dateStr)?.[1] ?? []
        const dateParlays = parlaysByDate[dateStr] ?? []
        const stats = dateStats[dateStr]
        const isExpanded = expandedDate === dateStr
        const dateObj = new Date(dateStr + 'T12:00:00')
        const formatted = dateObj.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
        const hitRate = stats?.validated > 0 ? Math.round((stats.hits / stats.validated) * 100) : null
        const parlayWins = dateParlays.filter(p => p.result === 'win').length
        const parlayTotal = dateParlays.filter(p => p.result !== 'pending').length

        return (
          <div key={dateStr} className="rounded-xl border border-border overflow-hidden">
            <button
              onClick={() => setExpandedDate(isExpanded ? null : dateStr)}
              className="w-full flex items-center justify-between px-4 py-3 bg-surface hover:bg-surface-hover transition-colors"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-text-primary">{formatted}</span>
                {datePicks.length > 0 && (
                  <span className="text-xs text-text-muted">{datePicks.length} picks</span>
                )}
                {dateParlays.length > 0 && (
                  <span className="text-xs text-text-muted">{dateParlays.length} parlays</span>
                )}
                {hitRate !== null && (
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                    hitRate >= 50 ? 'bg-green-500/15 text-green-400' :
                    hitRate >= 33 ? 'bg-amber-500/15 text-amber-400' :
                    'bg-red-500/15 text-red-400'
                  }`}>
                    {stats.hits}/{stats.validated} hit
                  </span>
                )}
                {parlayTotal > 0 && (
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                    parlayWins > 0 ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
                  }`}>
                    {parlayWins}/{parlayTotal}W
                  </span>
                )}
                {(!stats || stats.validated === 0) && dateParlays.every(p => p.result === 'pending') && (
                  <span className="text-xs text-text-muted italic flex items-center gap-1">
                    <Clock className="w-3 h-3" /> Pending
                  </span>
                )}
              </div>
              {isExpanded ? <ChevronUp className="w-4 h-4 text-text-muted" /> : <ChevronDown className="w-4 h-4 text-text-muted" />}
            </button>

            <AnimatePresence>
              {isExpanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="px-4 py-3 space-y-4 border-t border-border/50">
                    {/* Top Goal Scorer Picks */}
                    {datePicks.length > 0 && (
                      <div>
                        <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Top Goal Picks</h4>
                        <div className="space-y-1.5">
                          {datePicks.map((pick, i) => (
                            <div
                              key={`${pick.player_name}-${i}`}
                              className={`flex items-center gap-3 text-sm py-2 px-3 rounded-lg border ${
                                pick.validated
                                  ? pick.goal_hit
                                    ? 'bg-green-500/5 border-green-500/20'
                                    : 'bg-red-500/5 border-red-500/20'
                                  : 'border-border/50 bg-surface/50'
                              }`}
                            >
                              <ResultIcon hit={pick.goal_hit} />
                              <div className="flex-1 min-w-0">
                                <span className="font-semibold text-text-primary">{pick.player_name}</span>
                                <span className="text-xs text-text-muted ml-2">
                                  {pick.team} {pick.is_home ? 'vs' : '@'} {pick.opponent}
                                </span>
                              </div>
                              <span className="text-sm font-mono font-semibold text-ice">
                                {Math.round(pick.prob_goal * 100)}%
                              </span>
                              <ConfidenceBadge confidence={pick.confidence} />
                              {pick.validated && (
                                <span className="text-xs text-text-secondary font-medium w-14 text-right">
                                  {pick.actual_goals}G {pick.actual_assists}A
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Parlays */}
                    {dateParlays.length > 0 && (
                      <div>
                        <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Parlays</h4>
                        <div className="space-y-2">
                          {dateParlays.map((parlay, pi) => (
                            <div
                              key={`${parlay.name}-${pi}`}
                              className={`rounded-lg border p-3 ${
                                parlay.result === 'win' ? 'bg-green-500/5 border-green-500/20' :
                                parlay.result === 'loss' ? 'bg-red-500/5 border-red-500/20' :
                                'bg-surface/50 border-border/50'
                              }`}
                            >
                              <div className="flex items-center justify-between mb-2">
                                <span className="text-sm font-semibold text-text-primary">{parlay.name}</span>
                                <div className="flex items-center gap-2">
                                  {parlay.legs_hit !== null && parlay.legs_total !== null && (
                                    <span className="text-xs text-text-muted">
                                      {parlay.legs_hit}/{parlay.legs_total} legs
                                    </span>
                                  )}
                                  <ParlayResultBadge result={parlay.result} />
                                </div>
                              </div>
                              <div className="space-y-1">
                                {parlay.legs.map((leg, li) => (
                                  <div key={li} className="flex items-center gap-2 text-xs">
                                    <LegTypeLabel type={leg.leg_type} />
                                    {leg.hit !== null && leg.hit !== undefined ? (
                                      leg.hit
                                        ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
                                        : <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                                    ) : (
                                      <Clock className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
                                    )}
                                    <span className="text-text-primary flex-1 truncate">
                                      {leg.player_name ?? `${leg.team} win`}
                                    </span>
                                    <span className="text-text-muted">
                                      {leg.team}{leg.opponent ? ` vs ${leg.opponent}` : ''}
                                    </span>
                                    <span className="font-mono text-text-secondary">
                                      {Math.round(leg.probability * 100)}%
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )
      })}
    </div>
  )
}

/* ─── Tab: Performance Stats ─── */

function StatsTab({ summary, calibration }: { summary: Summary | null; calibration: CalibrationData }) {
  if (!summary || summary.validated === 0) {
    return (
      <div className="text-center py-12">
        <BarChart3 className="w-12 h-12 text-text-muted mx-auto mb-3 opacity-40" />
        <p className="text-sm text-text-muted">No validated predictions yet</p>
        <p className="text-xs text-text-muted mt-1">Stats appear after games are played and outcomes are recorded</p>
      </div>
    )
  }

  const goalRate = summary.goal_hit_rate ? Math.round(summary.goal_hit_rate * 100) : 0
  const pointRate = summary.point_hit_rate ? Math.round(summary.point_hit_rate * 100) : 0
  const brierScore = calibration?.brier_score
  const brierLabel = calibration?.interpretation ?? ''

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          label="Goal Pick Accuracy"
          value={`${goalRate}%`}
          sub={`${summary.goal_hits} / ${summary.validated} picks`}
          icon={Target}
          color="bg-ice/15 text-ice"
        />
        <StatCard
          label="Point Pick Accuracy"
          value={`${pointRate}%`}
          sub={`${summary.point_hits} / ${summary.validated} picks`}
          icon={Award}
          color="bg-green-500/15 text-green-400"
        />
        {brierScore !== undefined && brierScore !== null && (
          <StatCard
            label="Brier Score"
            value={brierScore.toFixed(3)}
            sub={brierLabel}
            icon={BarChart3}
            color={brierScore < 0.2 ? 'bg-green-500/15 text-green-400' : 'bg-amber-500/15 text-amber-400'}
          />
        )}
        <StatCard
          label="Sample Size"
          value={String(summary.validated)}
          sub={`top picks only`}
          icon={Zap}
          color="bg-purple-500/15 text-purple-400"
        />
      </div>

      <div className="rounded-xl border border-border bg-surface/50 p-4">
        <h4 className="text-sm font-semibold text-text-primary mb-2">How to read these stats</h4>
        <div className="space-y-2 text-xs text-text-muted">
          <p><strong className="text-text-secondary">Goal Pick Accuracy</strong> — Hit rate on our top 3 daily goal picks. Measured against only the highest-confidence selections, not every player in the league.</p>
          <p><strong className="text-text-secondary">Brier Score</strong> — Probability calibration (0 = perfect, 1 = worst). Below 0.20 means model probabilities are meaningful.</p>
          <p><strong className="text-text-secondary">Calibration</strong> — When we say &quot;40% chance to score,&quot; does that player actually score ~40% of the time? Check the Calibration tab.</p>
        </div>
      </div>
    </div>
  )
}

/* ─── Tab: Calibration ─── */

function CalibrationTab({ calibration }: { calibration: CalibrationData }) {
  if (!calibration || !calibration.goal_calibration?.length) {
    return (
      <div className="text-center py-12">
        <TrendingUp className="w-12 h-12 text-text-muted mx-auto mb-3 opacity-40" />
        <p className="text-sm text-text-muted">Not enough data for calibration analysis</p>
        <p className="text-xs text-text-muted mt-1">Need more validated predictions to generate calibration metrics</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 p-4 rounded-xl border border-border bg-surface/50">
        <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${
          calibration.brier_score < 0.2 ? 'bg-green-500/15' : 'bg-amber-500/15'
        }`}>
          <TrendingUp className={`w-6 h-6 ${
            calibration.brier_score < 0.2 ? 'text-green-400' : 'text-amber-400'
          }`} />
        </div>
        <div>
          <p className="text-lg font-bold text-text-primary">Brier Score: {calibration.brier_score.toFixed(3)}</p>
          <p className="text-xs text-text-muted">{calibration.interpretation}</p>
        </div>
      </div>

      <div>
        <h4 className="text-sm font-semibold text-text-primary mb-1">Predicted vs Actual Goal Rates</h4>
        <p className="text-xs text-text-muted mb-3">
          Blue = predicted probability, colored bar = actual hit rate. Green = well-calibrated (&le;5% off), amber = miscalibrated.
        </p>
        <div className="space-y-2">
          {calibration.goal_calibration.map((bucket, i) => (
            <CalibrationBar
              key={i}
              predicted={bucket.predicted}
              actual={bucket.actual}
              sampleSize={bucket.sample_size}
            />
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-border bg-surface/50 p-4">
        <h4 className="text-sm font-semibold text-text-primary mb-2">What is calibration?</h4>
        <p className="text-xs text-text-muted">
          A well-calibrated model means its probabilities are honest. If the model says a player has a 35% chance of scoring, players in that bucket should actually score about 35% of the time. Perfect calibration = predicted matches actual across all probability ranges.
        </p>
      </div>
    </div>
  )
}
