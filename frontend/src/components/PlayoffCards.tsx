'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Trophy, Swords, Flame, TrendingUp } from 'lucide-react'
import { api } from '@/lib/api'

type Series = {
  team_a: string
  team_b: string
  team_a_wins: number
  team_b_wins: number
  games_played: number
  status: 'in_progress' | 'scheduled' | 'complete'
  winner: string | null
  next_game_date: string | null
}

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

type HotTeam = {
  team: string
  games: number
  wins: number
  losses: number
  goal_diff: number
}

type Pick = {
  player_name: string
  team: string
  opponent: string
  is_home: boolean
  game_date: string
  market: string
  line: string
  probability: number
  confidence: 'high' | 'medium' | 'low'
  opponent_goalie: string | null
}

const ROUND_LABEL: Record<number, string> = {
  1: 'First Round',
  2: 'Second Round',
  3: 'Conference Finals',
  4: 'Stanley Cup Final',
}

function formatShortDate(iso: string): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const target = new Date(y, m - 1, d)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diffDays = Math.round((target.getTime() - today.getTime()) / 86_400_000)
  if (diffDays === 0) return 'Tonight'
  if (diffDays === 1) return 'Tomorrow'
  return target.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function SeriesRow({ s }: { s: Series }) {
  const leader =
    s.team_a_wins > s.team_b_wins ? s.team_a : s.team_b_wins > s.team_a_wins ? s.team_b : null
  const tied = s.team_a_wins === s.team_b_wins && s.games_played > 0

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
        ) : tied ? (
          <span className="text-[10px] uppercase tracking-wide text-text-muted">tied</span>
        ) : s.status === 'scheduled' ? (
          <span className="text-[10px] uppercase tracking-wide text-text-muted">upcoming</span>
        ) : null}
      </div>
    </div>
  )
}

export function PlayoffCards() {
  const [active, setActive] = useState<boolean | null>(null)
  const [bracket, setBracket] = useState<{ round: number; series: Series[] } | null>(null)
  const [overview, setOverview] = useState<{
    games_completed: number
    avg_goals_per_game: number
    top_scorers: TopScorer[]
    hottest_teams: HotTeam[]
  } | null>(null)
  const [picks, setPicks] = useState<Pick[]>([])
  const [betsWindow, setBetsWindow] = useState<{ days: number; games: number }>({
    days: 3,
    games: 0,
  })

  useEffect(() => {
    api.getPlayoffStatus().then(async s => {
      if (!s.is_active) {
        setActive(false)
        return
      }
      setActive(true)
      const [b, o, p] = await Promise.all([
        api.getPlayoffBracket(),
        api.getPlayoffOverview(),
        api.getPlayoffBestBets(3, 8),
      ])
      setBracket({ round: b.round, series: b.series })
      setOverview({
        games_completed: o.games_completed,
        avg_goals_per_game: o.avg_goals_per_game,
        top_scorers: o.top_scorers,
        hottest_teams: o.hottest_teams,
      })
      setPicks(p.picks)
      setBetsWindow({ days: p.days, games: p.games })
    })
  }, [])

  if (active !== true) return null

  const roundLabel = bracket ? ROUND_LABEL[bracket.round] ?? 'Playoffs' : 'Playoffs'

  return (
    <motion.div
      initial={{ y: 20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.42 }}
      className="w-full max-w-4xl mb-4"
    >
      <div className="flex items-center gap-2 justify-center mb-3">
        <Trophy className="w-4 h-4 text-amber-500" />
        <span className="text-xs font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
          Stanley Cup Playoffs · {roundLabel}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Bracket card */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <Swords className="w-4 h-4 text-primary dark:text-ice" />
            <p className="text-xs font-semibold text-text-primary uppercase tracking-wider">
              Bracket
            </p>
            <span className="ml-auto text-[10px] text-text-muted">
              {bracket?.series.length ?? 0} series
            </span>
          </div>
          <div className="space-y-0 flex-1 overflow-hidden">
            {bracket && bracket.series.length > 0 ? (
              bracket.series.slice(0, 8).map(s => (
                <SeriesRow key={`${s.team_a}-${s.team_b}`} s={s} />
              ))
            ) : (
              <p className="text-xs text-text-muted italic">Bracket syncing…</p>
            )}
          </div>
        </div>

        {/* Overview card */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4 text-primary dark:text-ice" />
            <p className="text-xs font-semibold text-text-primary uppercase tracking-wider">
              Overview
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
                  {overview.top_scorers.slice(0, 4).map(p => (
                    <div key={p.player_id} className="flex items-center justify-between text-xs">
                      <span className="truncate text-text-primary font-medium">{p.name}</span>
                      <span className="text-text-muted font-mono tabular-nums flex-shrink-0 ml-2">
                        {p.points}P ({p.goals}G)
                      </span>
                    </div>
                  ))}
                  {overview.top_scorers.length === 0 && (
                    <p className="text-xs text-text-muted italic">No boxscores yet</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-text-muted italic">Loading…</p>
          )}
        </div>

        {/* Best bets card */}
        <div className="rounded-2xl border border-border bg-surface shadow-card p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <Flame className="w-4 h-4 text-amber-500" />
            <p className="text-xs font-semibold text-text-primary uppercase tracking-wider">
              Most Likely Bets
            </p>
            <span className="ml-auto text-[10px] text-text-muted">
              next {betsWindow.days}d · {betsWindow.games} gm
            </span>
          </div>
          <div className="space-y-2 flex-1">
            {picks.length > 0 ? (
              picks.slice(0, 5).map((p, i) => (
                <div
                  key={`${p.player_name}-${i}`}
                  className="flex items-center gap-2 py-1.5 border-b border-border/50 last:border-0"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-text-primary truncate">
                      {p.player_name}
                    </p>
                    <p className="text-[10px] text-text-muted truncate">
                      {formatShortDate(p.game_date)} · {p.team} {p.is_home ? 'vs' : '@'} {p.opponent} · {p.line}
                    </p>
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <p className="text-xs font-bold text-primary dark:text-ice tabular-nums">
                      {Math.round(p.probability * 100)}%
                    </p>
                    <p className="text-[9px] uppercase tracking-wide text-text-muted">
                      {p.confidence}
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <p className="text-xs text-text-muted italic">No playoff games in the window</p>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  )
}
