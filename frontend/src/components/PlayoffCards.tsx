'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Trophy, Flame, TrendingUp, Calendar } from 'lucide-react'
import { api } from '@/lib/api'
import type { BracketSeries, ConferenceBracket } from '@/lib/api'
import { BracketDiagram } from './BracketDiagram'
import { LockedPicksCard, type LockedPick } from './LockedPicksCard'

// Series shape from the bracket endpoint. Kept narrow here; the
// BracketDiagram component uses the richer BracketSeries from api.ts.
type Series = BracketSeries

type TopScorer = {
  player_id: number
  name: string
  team: string
  games: number
  goals: number
  assists: number
  points: number
  ppg: number
}

type Pick = {
  date: string
  rank: number
  player_name: string
  team: string
  opponent: string
  is_home: boolean
  market: string
  line: string
  probability: number
  confidence: 'high' | 'medium' | 'low'
  hit: boolean | null
  actual_goals: number | null
  actual_points: number | null
  graded: boolean
}

type PickRecord = {
  graded: number
  hits: number
  misses: number
  hit_rate: number | null
  streak: { kind: 'hit' | 'miss'; length: number } | null
}

const ROUND_LABEL: Record<number, string> = {
  1: 'First Round',
  2: 'Second Round',
  3: 'Conference Finals',
  4: 'Stanley Cup Final',
}

// Short-form date used inside the bracket rows. Always renders in the
// viewer's local timezone so a 7pm EST puck drop reads as 7pm to a fan
// in Toronto and 4pm to one in LA.
//
// iOS Safari quirk: `new Date("2026-05-22")` (date-only ISO) is parsed as
// UTC midnight, which on the US East Coast renders as "May 21, 8pm" the
// night before. When we only have a date string (no time), parse it as a
// local-zone calendar date instead.
function formatGameDate(iso: string | null, time: string | null): string {
  let d: Date | null = null

  if (time) {
    const parsed = new Date(time)
    if (!Number.isNaN(parsed.getTime())) d = parsed
  }
  if (!d && iso) {
    // Try date-only format first
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
    if (m) {
      d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    } else {
      const parsed = new Date(iso)
      if (!Number.isNaN(parsed.getTime())) d = parsed
    }
  }
  if (!d) return ''

  const today = new Date()
  const tomorrow = new Date()
  tomorrow.setDate(today.getDate() + 1)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  if (sameDay(d, today))
    return time ? `Today ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : 'Today'
  if (sameDay(d, tomorrow))
    return time ? `Tom ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : 'Tomorrow'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function SeriesRow({ s }: { s: Series }) {
  const leader =
    s.team_a_wins > s.team_b_wins ? s.team_a : s.team_b_wins > s.team_a_wins ? s.team_b : null
  const tied = s.team_a_wins === s.team_b_wins && s.games_played > 0
  const dateLabel = formatGameDate(s.next_game_date, s.next_game_time)

  return (
    <div className="flex items-center justify-between gap-2 py-1.5 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        <span
          className={`text-xs font-bold w-9 ${
            leader === s.team_a ? 'text-primary dark:text-ice' : 'text-text-secondary'
          }`}
        >
          {s.team_a}
        </span>
        <span className="text-xs text-text-muted">vs</span>
        <span
          className={`text-xs font-bold w-9 ${
            leader === s.team_b ? 'text-primary dark:text-ice' : 'text-text-secondary'
          }`}
        >
          {s.team_b}
        </span>
      </div>
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <span className="text-xs font-mono tabular-nums text-text-primary">
          {s.team_a_wins}-{s.team_b_wins}
        </span>
        {s.status === 'complete' && s.winner ? (
          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-green-500/10 text-green-600 dark:text-green-400 font-semibold">
            {s.winner} won
          </span>
        ) : s.status === 'in_progress' && tied ? (
          <span className="text-[10px] uppercase tracking-wide text-amber-600 dark:text-amber-400 font-semibold">
            tied
          </span>
        ) : s.status === 'in_progress' && leader ? (
          <span className="text-[10px] uppercase tracking-wide text-primary dark:text-ice font-semibold">
            {leader} leads
          </span>
        ) : s.status === 'scheduled' && dateLabel ? (
          <span className="text-[10px] uppercase tracking-wide text-text-muted">
            {dateLabel}
          </span>
        ) : s.status === 'scheduled' ? (
          <span className="text-[10px] uppercase tracking-wide text-text-muted">upcoming</span>
        ) : null}
      </div>
    </div>
  )
}

// Shape returned by the status check; mirrors the backend response so we
// can also pick up next_game_date in the same trip.
type StatusResult = Awaited<ReturnType<typeof api.getPlayoffStatus>>

function BracketSkeleton() {
  return (
    <div className="w-full max-w-4xl mb-4 animate-pulse" aria-hidden="true">
      <div className="h-3 w-56 mx-auto mb-3 rounded bg-border/60" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {[0, 1, 2].map(i => (
          <div key={i} className="rounded-2xl border border-border bg-surface shadow-card p-4">
            <div className="h-3 w-24 mb-3 rounded bg-border/60" />
            {[0, 1, 2, 3].map(j => (
              <div key={j} className="flex items-center justify-between py-1.5">
                <div className="h-3 w-16 rounded bg-border/40" />
                <div className="h-3 w-10 rounded bg-border/40" />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

// Empty conference bracket - used when bracket_view isn't yet populated
// (cold start, or pre-postseason).
const EMPTY_CONF: ConferenceBracket = {
  round_1: [],
  round_2: [],
  conference_final: null,
  champion: null,
}

export function PlayoffCards() {
  const [active, setActive] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)
  const [bracket, setBracket] = useState<{
    round: number
    series: Series[]
    view: {
      championship: BracketSeries | null
      east: ConferenceBracket
      west: ConferenceBracket
    } | null
  } | null>(null)
  const [overview, setOverview] = useState<{
    games_completed: number
    avg_goals_per_game: number
    top_scorers: TopScorer[]
  } | null>(null)
  const [picks, setPicks] = useState<Pick[]>([])
  const [record, setRecord] = useState<PickRecord | null>(null)
  const [nextGameDate, setNextGameDate] = useState<string | null>(null)

  useEffect(() => {
    // Cleanup-on-unmount guard: if the user navigates away mid-fetch (or
    // React strict-mode double-invokes the effect in dev), don't try to
    // setState on a torn-down component.
    let cancelled = false

    async function load() {
      let s: StatusResult
      try {
        s = await api.getPlayoffStatus()
      } catch {
        if (!cancelled) {
          setActive(false)
          setLoading(false)
        }
        return
      }
      if (cancelled) return
      if (!s.is_active) {
        setActive(false)
        setLoading(false)
        return
      }
      setActive(true)
      setNextGameDate(s.next_game_date ?? null)

      // Parallel-fetch the four sub-resources. Each is wrapped in its own
      // try so one broken endpoint doesn't blank the whole section.
      const [b, o, p, r] = await Promise.all([
        api.getPlayoffBracket().catch(() => null),
        api.getPlayoffOverview().catch(() => null),
        api.getPlayoffPicks().catch(() => null),
        api.getPlayoffPickRecord(30).catch(() => null),
      ])
      if (cancelled) return

      if (b)
        setBracket({
          round: b.round,
          series: b.series,
          view: b.bracket_view
            ? {
                championship: b.bracket_view.championship,
                east: b.bracket_view.east,
                west: b.bracket_view.west,
              }
            : null,
        })
      if (o)
        setOverview({
          games_completed: o.games_completed,
          avg_goals_per_game: o.avg_goals_per_game,
          top_scorers: o.top_scorers,
        })
      if (p) setPicks(p.picks)
      if (r)
        setRecord({
          graded: r.graded,
          hits: r.hits,
          misses: r.misses,
          hit_rate: r.hit_rate,
          streak: r.streak,
        })
      setLoading(false)
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) return <BracketSkeleton />
  if (active !== true) return null

  const roundLabel = bracket ? ROUND_LABEL[bracket.round] ?? 'Playoffs' : 'Playoffs'

  // "Live" series means at least one is in_progress and we have boxscore data.
  const liveSeriesCount =
    bracket?.series.filter(s => s.status === 'in_progress').length ?? 0

  // Use the structured bracket_view if present; otherwise show empty
  // conferences (BracketDiagram handles all empty/TBD states).
  const view = bracket?.view ?? {
    championship: null,
    east: EMPTY_CONF,
    west: EMPTY_CONF,
  }

  // Cast picks to the LockedPick shape - lock_status, start_time_utc etc.
  // are optional on the Pick type and the LockedPicksCard tolerates missing.
  const lockedPicks: LockedPick[] = picks as unknown as LockedPick[]

  return (
    <motion.div
      initial={{ y: 20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.42 }}
      className="w-full max-w-6xl mb-4 space-y-3"
    >
      {/* Section heading */}
      <div className="flex items-center gap-2 justify-center">
        <Trophy className="w-4 h-4 text-amber-500" />
        <span className="text-xs font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
          Stanley Cup Playoffs · {roundLabel}
        </span>
        {liveSeriesCount > 0 && (
          <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-red-500 font-semibold">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            {liveSeriesCount} live
          </span>
        )}
      </div>

      {/* Tournament bracket diagram - the hero element on the page */}
      <BracketDiagram
        championship={view.championship}
        east={view.east}
        west={view.west}
      />

      {/* Locked picks + overview row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {/* Tonight's picks (with 2h lock-in treatment) - takes 2/3 width */}
        <div className="lg:col-span-2">
          <LockedPicksCard picks={lockedPicks} fallbackNextGameDate={nextGameDate} />
          {record && record.graded > 0 && (
            <div className="mt-2 flex items-center justify-between rounded-lg bg-surface/60 border border-border/60 px-3 py-2 text-xs">
              <div className="flex items-center gap-2 text-text-secondary">
                <Flame
                  className={`w-3.5 h-3.5 ${
                    record.streak && record.streak.kind === 'hit'
                      ? 'text-amber-500'
                      : 'text-text-muted'
                  }`}
                />
                <span>
                  Model record · last 30 days:{' '}
                  <span className="font-mono tabular-nums font-semibold text-text-primary">
                    {record.hits}-{record.misses}
                  </span>
                  {record.hit_rate !== null && (
                    <span className="text-text-muted">
                      {' '}
                      · {Math.round(record.hit_rate * 100)}% hit rate
                    </span>
                  )}
                </span>
              </div>
              {record.streak && record.streak.length >= 2 && (
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  {record.streak.length}-pick {record.streak.kind} streak
                </span>
              )}
            </div>
          )}
        </div>

        {/* Overview card - takes 1/3 width */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4 text-primary dark:text-ice" />
            <p className="text-xs font-semibold text-text-primary uppercase tracking-wider">
              Postseason Pulse
            </p>
          </div>
          {overview ? (
            <div className="space-y-3 flex-1">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg bg-background/50 dark:bg-surface/50 border border-border/50 p-2">
                  <p className="text-[10px] text-text-muted uppercase">Games</p>
                  <p className="text-sm font-bold text-text-primary">
                    {overview.games_completed}
                  </p>
                </div>
                <div className="rounded-lg bg-background/50 dark:bg-surface/50 border border-border/50 p-2">
                  <p className="text-[10px] text-text-muted uppercase">G/Gm</p>
                  <p className="text-sm font-bold text-text-primary">
                    {overview.avg_goals_per_game.toFixed(2)}
                  </p>
                </div>
              </div>
              <div>
                <p className="text-[10px] text-text-muted uppercase mb-1">Top scorers</p>
                <div className="space-y-1">
                  {overview.top_scorers.slice(0, 5).map(p => (
                    <div key={p.player_id} className="flex items-center justify-between text-xs">
                      <span className="truncate text-text-primary font-medium">{p.name}</span>
                      <span className="text-text-muted font-mono tabular-nums flex-shrink-0 ml-2">
                        {p.points}P ({p.goals}G)
                      </span>
                    </div>
                  ))}
                  {overview.top_scorers.length === 0 &&
                    (overview.games_completed === 0 ? (
                      <div className="flex items-start gap-1.5 text-xs text-text-muted italic">
                        <Calendar className="w-3 h-3 mt-0.5 flex-shrink-0" />
                        <span>
                          {nextGameDate
                            ? `Round opens ${formatGameDate(nextGameDate, null)} — leaderboard fills in after Game 1`
                            : 'Leaderboard fills in once Game 1 wraps'}
                        </span>
                      </div>
                    ) : (
                      <p className="text-xs text-text-muted italic">No boxscores yet</p>
                    ))}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-text-muted italic">Loading…</p>
          )}
        </div>
      </div>
    </motion.div>
  )
}
