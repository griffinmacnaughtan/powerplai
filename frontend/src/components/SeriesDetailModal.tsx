'use client'

/**
 * Series detail modal.
 *
 * Opens when a fan clicks a series in the bracket diagram. Pulls
 * comprehensive detail from /api/playoffs/series/{a}/{b}: game-by-game
 * results, upcoming games, per-team top playoff scorers, goalies, and
 * the model's picks tied to the next game in the series.
 *
 * Design choices
 * - Portaled to document.body so the modal's z-index always wins regardless
 *   of where it's mounted in the tree.
 * - Click-outside / Escape to dismiss.
 * - Body scroll locked while open.
 * - All content fits a single mental model: header > games > picks > stats.
 *   The user opens to learn "what's the story of this matchup" and the
 *   page answers in one scroll on mobile.
 */
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Trophy, Calendar, MapPin, Target, Shield, Flame } from 'lucide-react'
import { api } from '@/lib/api'
import type {
  BracketSeries, SeriesDetail, SeriesGame, SeriesScorer, SeriesGoalie,
} from '@/lib/api'

type Props = {
  /** Bracket series clicked. Open when not null, closed when null. */
  series: BracketSeries | null
  onClose: () => void
}

const ROUND_LABEL: Record<number, string> = {
  1: 'First Round',
  2: 'Second Round',
  3: 'Conference Final',
  4: 'Stanley Cup Final',
}

function formatGameDateTime(iso: string | null): string {
  if (!iso) return 'TBD'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'TBD'
  return d.toLocaleString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function GameRow({ g, leftTeam, rightTeam }: {
  g: SeriesGame
  leftTeam: string
  rightTeam: string
}) {
  const leftScore = g.home_team === leftTeam ? g.home_score : g.away_score
  const rightScore = g.home_team === leftTeam ? g.away_score : g.home_score
  const leftWon = g.winner === leftTeam
  const rightWon = g.winner === rightTeam
  const isHomeLeft = g.home_team === leftTeam
  const isLive = (g.state || '').toUpperCase() === 'LIVE' || (g.state || '').toUpperCase() === 'CRIT'

  return (
    <div
      className={`flex items-center justify-between gap-3 py-2 px-3 rounded-lg border ${
        isLive
          ? 'border-red-400/60 bg-red-500/5'
          : g.is_completed
            ? 'border-border bg-background/40'
            : 'border-dashed border-border/60 bg-background/20'
      }`}
    >
      <div className="flex-shrink-0 w-12 text-[10px] text-text-muted">
        {g.game_date ? new Date(g.game_date + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : 'TBD'}
      </div>
      <div className="flex-1 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className={`text-right text-sm font-bold ${
          leftWon ? 'text-green-700 dark:text-green-400'
            : rightWon ? 'text-text-muted line-through decoration-text-muted/40'
            : 'text-text-primary'
        }`}>
          {leftTeam} {isHomeLeft ? '' : '@'}
        </div>
        {g.is_completed ? (
          <div className="font-mono tabular-nums text-sm text-text-primary px-2">
            {leftScore}-{rightScore}
          </div>
        ) : isLive ? (
          <div className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 dark:text-red-400 px-2">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            Live
          </div>
        ) : (
          <div className="text-[10px] text-text-muted px-2">
            {g.start_time_utc ? new Date(g.start_time_utc).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''}
          </div>
        )}
        <div className={`text-left text-sm font-bold ${
          rightWon ? 'text-green-700 dark:text-green-400'
            : leftWon ? 'text-text-muted line-through decoration-text-muted/40'
            : 'text-text-primary'
        }`}>
          {isHomeLeft ? '@' : ''} {rightTeam}
        </div>
      </div>
      {g.venue && (
        <div className="hidden sm:flex items-center gap-1 text-[10px] text-text-muted flex-shrink-0">
          <MapPin className="w-3 h-3" />
          <span className="truncate max-w-[110px]">{g.venue}</span>
        </div>
      )}
    </div>
  )
}

function ScorerTable({ team, scorers }: { team: string; scorers: SeriesScorer[] }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-bold uppercase tracking-wider text-text-primary">{team}</p>
      {scorers.length === 0 ? (
        <p className="text-xs text-text-muted italic">No boxscores yet</p>
      ) : (
        <div className="space-y-1">
          {scorers.map(p => (
            <div key={p.player_id} className="flex items-center justify-between text-xs gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-text-primary">
                  {p.name}
                  {p.position && <span className="ml-1 text-text-muted text-[10px]">{p.position}</span>}
                </p>
              </div>
              <div className="font-mono tabular-nums text-text-secondary flex-shrink-0 text-[11px]">
                <span className="font-bold text-text-primary">{p.points}</span>P{' '}
                <span className="text-text-muted">({p.goals}G {p.assists}A)</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function GoalieTable({ team, goalies }: { team: string; goalies: SeriesGoalie[] }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-bold uppercase tracking-wider text-text-primary">{team}</p>
      {goalies.length === 0 ? (
        <p className="text-xs text-text-muted italic">No goalie data</p>
      ) : (
        <div className="space-y-1">
          {goalies.map(g => (
            <div key={g.name} className="flex items-center justify-between text-xs gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-text-primary">{g.name}</p>
                <p className="text-[10px] text-text-muted">
                  {g.wins}-{g.losses} · {g.games_played} GP
                </p>
              </div>
              <div className="font-mono tabular-nums text-text-secondary flex-shrink-0 text-[11px] text-right">
                {g.save_pct != null && (
                  <p className="font-bold text-text-primary">{g.save_pct.toFixed(3)}</p>
                )}
                {g.gaa != null && (
                  <p className="text-[10px] text-text-muted">{g.gaa.toFixed(2)} GAA</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ModalBody({ data, onClose }: { data: SeriesDetail; onClose: () => void }) {
  // Parent only renders ModalBody when data.series is non-null, but the
  // narrowing helper here keeps TS happy without violating the rules of
  // hooks (no conditional return before hook calls).
  const s = data.series!
  const roundLabel = s.round ? ROUND_LABEL[s.round] : 'Playoff Series'
  const confLabel = s.conference === 'east'
    ? 'Eastern Conference'
    : s.conference === 'west'
      ? 'Western Conference'
      : ''

  const completedGames = useMemo(() => s.games.filter(g => g.is_completed), [s.games])

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97, y: 20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97, y: 10 }}
      transition={{ type: 'spring', stiffness: 280, damping: 28 }}
      className="relative w-full max-w-3xl max-h-[90vh] flex flex-col rounded-2xl bg-surface border border-border shadow-2xl overflow-hidden"
      onClick={e => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex-shrink-0 p-5 border-b border-border">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <Trophy className="w-3.5 h-3.5 text-amber-500" />
              <p className="text-[11px] uppercase tracking-wider text-text-muted font-semibold">
                {confLabel}{confLabel && ' · '}{roundLabel}
              </p>
            </div>
            <div className="flex items-center gap-3 mt-0.5">
              <div className="flex items-baseline gap-2">
                <h2 className={`text-2xl md:text-3xl font-extrabold tracking-tight ${
                  s.leader === s.team_a ? 'text-primary dark:text-ice' : 'text-text-primary'
                }`}>
                  {s.team_a}
                </h2>
                <span className="font-mono tabular-nums text-2xl md:text-3xl font-bold text-text-secondary">
                  {s.team_a_wins}
                </span>
              </div>
              <span className="text-text-muted text-lg">vs</span>
              <div className="flex items-baseline gap-2">
                <span className="font-mono tabular-nums text-2xl md:text-3xl font-bold text-text-secondary">
                  {s.team_b_wins}
                </span>
                <h2 className={`text-2xl md:text-3xl font-extrabold tracking-tight ${
                  s.leader === s.team_b ? 'text-primary dark:text-ice' : 'text-text-primary'
                }`}>
                  {s.team_b}
                </h2>
              </div>
              {s.status === 'in_progress' && (
                <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 dark:text-red-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  Live
                </span>
              )}
              {s.status === 'complete' && s.winner && (
                <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-green-500/10 text-green-700 dark:text-green-400">
                  <Trophy className="w-2.5 h-2.5" />
                  {s.winner} advance
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close series detail"
            className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-background/60 dark:hover:bg-surface/60 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Next game preview strip */}
        {s.next_game && (
          <div className="mt-4 flex items-center gap-2 rounded-lg bg-primary/5 dark:bg-ice/5 border border-primary/20 dark:border-ice/20 px-3 py-2 text-xs">
            <Calendar className="w-3.5 h-3.5 text-primary dark:text-ice flex-shrink-0" />
            <span className="font-semibold text-text-primary">
              Next: Game {(completedGames.length || 0) + 1}
            </span>
            <span className="text-text-secondary">
              · {formatGameDateTime(s.next_game.start_time_utc)}
            </span>
            {s.next_game.venue && (
              <span className="hidden sm:inline text-text-muted">
                · {s.next_game.venue}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Body: scrollable */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        {/* Game-by-game results */}
        <section>
          <h3 className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2 flex items-center gap-2">
            <Flame className="w-3.5 h-3.5 text-amber-500" />
            Series Games
          </h3>
          <div className="space-y-1.5">
            {s.games.length === 0 ? (
              <p className="text-xs text-text-muted italic">No games scheduled yet</p>
            ) : (
              s.games.map((g, i) => (
                <GameRow
                  key={g.game_id || i}
                  g={g}
                  leftTeam={s.team_a}
                  rightTeam={s.team_b}
                />
              ))
            )}
          </div>
        </section>

        {/* Next-game picks */}
        {s.next_game_picks.length > 0 && (
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2 flex items-center gap-2">
              <Target className="w-3.5 h-3.5 text-amber-500" />
              Model picks for next game
            </h3>
            <div className="space-y-1.5">
              {s.next_game_picks.map(p => (
                <div
                  key={p.rank}
                  className="flex items-center gap-2 rounded-lg border border-border bg-background/40 px-3 py-2"
                >
                  <span className="text-[10px] font-bold text-text-muted w-4 tabular-nums">
                    {p.rank}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-text-primary truncate">
                      {p.player_name}
                    </p>
                    <p className="text-[11px] text-text-secondary truncate">
                      <span className="font-semibold">{p.team}</span> · {p.market}
                      {p.opponent_goalie && (
                        <span className="text-text-muted"> · vs {p.opponent_goalie}</span>
                      )}
                    </p>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-sm font-bold text-primary dark:text-ice font-mono tabular-nums">
                      {Math.round(p.probability * 100)}%
                    </p>
                    <p className="text-[9px] uppercase tracking-wide text-text-muted">
                      {p.confidence}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Top scorers */}
        {(s.top_scorers[s.team_a]?.length || s.top_scorers[s.team_b]?.length) ? (
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2 flex items-center gap-2">
              <Flame className="w-3.5 h-3.5 text-primary dark:text-ice" />
              Top scorers in series
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <ScorerTable team={s.team_a} scorers={s.top_scorers[s.team_a] || []} />
              <ScorerTable team={s.team_b} scorers={s.top_scorers[s.team_b] || []} />
            </div>
          </section>
        ) : null}

        {/* Goalies */}
        {(s.goalies[s.team_a]?.length || s.goalies[s.team_b]?.length) ? (
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2 flex items-center gap-2">
              <Shield className="w-3.5 h-3.5 text-primary dark:text-ice" />
              Goalies
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <GoalieTable team={s.team_a} goalies={s.goalies[s.team_a] || []} />
              <GoalieTable team={s.team_b} goalies={s.goalies[s.team_b] || []} />
            </div>
          </section>
        ) : null}
      </div>
    </motion.div>
  )
}

export function SeriesDetailModal({ series, onClose }: Props) {
  const [detail, setDetail] = useState<SeriesDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch detail when series changes (or on open)
  useEffect(() => {
    if (!series) {
      setDetail(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getPlayoffSeries(series.team_a, series.team_b)
      .then(d => {
        if (cancelled) return
        if (!d || !d.series) {
          setError('Series detail unavailable')
        } else {
          setDetail(d)
        }
      })
      .catch(() => !cancelled && setError('Could not load series'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [series])

  // Escape to close + body scroll lock
  useEffect(() => {
    if (!series) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [series, onClose])

  if (typeof document === 'undefined') return null

  return createPortal(
    <AnimatePresence>
      {series && (
        <motion.div
          key="series-modal"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm p-2 sm:p-4"
          onClick={onClose}
          aria-modal="true"
          role="dialog"
          aria-labelledby="series-detail-title"
        >
          {loading && (
            <div
              className="relative w-full max-w-3xl rounded-2xl bg-surface border border-border shadow-2xl p-10 text-center text-sm text-text-muted"
              onClick={e => e.stopPropagation()}
            >
              <div className="inline-block w-5 h-5 rounded-full border-2 border-primary/30 border-t-primary dark:border-ice/30 dark:border-t-ice animate-spin mb-3" />
              <p>Loading series detail…</p>
            </div>
          )}
          {!loading && error && (
            <div
              className="relative w-full max-w-md rounded-2xl bg-surface border border-border shadow-2xl p-6 text-center"
              onClick={e => e.stopPropagation()}
            >
              <p className="text-sm text-text-primary mb-3">{error}</p>
              <button
                onClick={onClose}
                className="text-xs px-3 py-1.5 rounded-lg bg-primary text-white dark:bg-ice dark:text-background hover:opacity-90"
              >
                Close
              </button>
            </div>
          )}
          {!loading && !error && detail && (
            <ModalBody data={detail} onClose={onClose} />
          )}
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
