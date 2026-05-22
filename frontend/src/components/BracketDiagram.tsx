'use client'

/**
 * Tournament bracket diagram.
 *
 * Renders the NHL playoff bracket as a real tournament tree: Eastern
 * Conference on the left, Stanley Cup Final at the centre, Western
 * Conference on the right (desktop). On mobile we stack vertically:
 * East -> Championship -> West so a thumb can scroll the whole bracket
 * without zooming.
 *
 * Design notes
 * - Each series is a self-contained "SeriesNode" that handles its own
 *   styling for in-progress / leader / complete / empty states.
 * - The bracket is intentionally minimal on chrome: thin connectors,
 *   small typography, tabular team rows. The drama comes from the live
 *   indicator, the leader highlight, and the trophy on a champion.
 * - Connecting lines are pure CSS borders so they scale perfectly,
 *   never get out of sync with the boxes, and don't need SVG layout
 *   math at runtime.
 */
import { motion } from 'framer-motion'
import { Trophy, Flame } from 'lucide-react'
import type { BracketSeries, ConferenceBracket } from '@/lib/api'

type Props = {
  championship: BracketSeries | null
  east: ConferenceBracket
  west: ConferenceBracket
}

const ROUND_TITLES = ['First Round', 'Second Round', 'Conference Final', 'Stanley Cup Final']

function ScorePill({ s }: { s: BracketSeries }) {
  if (s.status === 'complete' && s.winner) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-700 dark:text-green-400 font-bold uppercase tracking-wide whitespace-nowrap">
        <Trophy className="w-2.5 h-2.5" />
        {s.winner}
      </span>
    )
  }
  if (s.status === 'in_progress') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-600 dark:text-red-400 font-bold uppercase tracking-wide whitespace-nowrap">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
        Live
      </span>
    )
  }
  return null
}

function SeriesNode({
  s,
  align = 'left',
}: {
  s: BracketSeries | null
  align?: 'left' | 'right' | 'center'
}) {
  if (!s) {
    // Empty slot - keeps the bracket layout intact between rounds while
    // we wait for matchups to be determined.
    return (
      <div
        className={`group relative w-full rounded-lg border border-dashed border-border/50 bg-background/40 dark:bg-surface/40 px-2.5 py-2 text-text-muted ${
          align === 'right' ? 'text-right' : ''
        }`}
      >
        <p className="text-[10px] italic">TBD</p>
      </div>
    )
  }

  const leader =
    s.team_a_wins > s.team_b_wins
      ? s.team_a
      : s.team_b_wins > s.team_a_wins
        ? s.team_b
        : null
  const completed = s.status === 'complete'
  const teamARow = (
    <div className="flex items-center justify-between gap-2 leading-tight">
      <span
        className={`font-bold text-xs ${
          leader === s.team_a
            ? completed
              ? 'text-green-700 dark:text-green-400'
              : 'text-primary dark:text-ice'
            : leader && leader !== s.team_a
              ? 'text-text-muted line-through decoration-text-muted/40'
              : 'text-text-primary'
        }`}
      >
        {s.team_a}
      </span>
      <span className="font-mono tabular-nums text-xs text-text-secondary">{s.team_a_wins}</span>
    </div>
  )
  const teamBRow = (
    <div className="flex items-center justify-between gap-2 leading-tight">
      <span
        className={`font-bold text-xs ${
          leader === s.team_b
            ? completed
              ? 'text-green-700 dark:text-green-400'
              : 'text-primary dark:text-ice'
            : leader && leader !== s.team_b
              ? 'text-text-muted line-through decoration-text-muted/40'
              : 'text-text-primary'
        }`}
      >
        {s.team_b}
      </span>
      <span className="font-mono tabular-nums text-xs text-text-secondary">{s.team_b_wins}</span>
    </div>
  )

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`relative w-full rounded-lg border ${
        s.status === 'in_progress'
          ? 'border-red-400/60 dark:border-red-500/50 bg-red-500/5 dark:bg-red-500/10'
          : completed
            ? 'border-border/60 bg-background/60 dark:bg-surface/60'
            : 'border-border bg-surface'
      } shadow-sm px-2.5 py-2 space-y-1`}
    >
      <div className="absolute -top-2 right-2">
        <ScorePill s={s} />
      </div>
      {teamARow}
      {teamBRow}
    </motion.div>
  )
}

function ChampionshipNode({ s, eastChamp, westChamp }: {
  s: BracketSeries | null
  eastChamp: string | null
  westChamp: string | null
}) {
  // Synthesize a placeholder series when the SCF hasn't started but
  // both conference champions are known, so fans see the upcoming
  // matchup take shape.
  let display: BracketSeries | null = s
  if (!display && eastChamp && westChamp) {
    display = {
      team_a: eastChamp,
      team_b: westChamp,
      team_a_wins: 0,
      team_b_wins: 0,
      games_played: 0,
      status: 'scheduled',
      winner: null,
      next_game_date: null,
      next_game_time: null,
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: 0.15, type: 'spring', stiffness: 200, damping: 20 }}
      className="relative"
    >
      <div className="flex items-center justify-center gap-1 mb-1.5">
        <Trophy className="w-3 h-3 text-amber-500" />
        <p className="text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
          Stanley Cup Final
        </p>
      </div>
      {display ? (
        <div
          className={`rounded-xl border-2 ${
            display.winner
              ? 'border-amber-500 bg-amber-500/10'
              : display.status === 'in_progress'
                ? 'border-red-400 bg-red-500/10'
                : 'border-amber-400/60 bg-amber-50/40 dark:bg-amber-500/5'
          } shadow-lg p-3 min-w-[150px]`}
        >
          <SeriesNode s={display} />
          {display.winner && (
            <div className="mt-2 text-center">
              <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
                <Trophy className="w-3 h-3" />
                Stanley Cup Champion
              </span>
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-xl border-2 border-dashed border-amber-400/40 bg-amber-50/20 dark:bg-amber-500/5 px-3 py-4 min-w-[150px] text-center">
          <p className="text-[10px] italic text-text-muted">Awaiting conference champions</p>
        </div>
      )}
    </motion.div>
  )
}

function RoundColumn({
  title,
  series,
  align,
  count,
}: {
  title: string
  series: (BracketSeries | null)[]
  align: 'left' | 'right'
  count: number
}) {
  // Pad with nulls if the round hasn't filled yet (e.g. only 2 of 4 R2 known)
  const padded = Array.from({ length: count }, (_, i) => series[i] ?? null)

  return (
    <div className="flex flex-col flex-1 min-w-0">
      <p
        className={`text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-2 ${
          align === 'right' ? 'text-right' : ''
        }`}
      >
        {title}
      </p>
      <div className="flex flex-col justify-around gap-2 h-full min-h-[160px]">
        {padded.map((s, i) => (
          <div key={i} className="relative">
            <SeriesNode s={s} align={align} />
          </div>
        ))}
      </div>
    </div>
  )
}

function ConferenceColumn({
  side,
  title,
  champion,
  bracket,
}: {
  side: 'east' | 'west'
  title: string
  champion: string | null
  bracket: ConferenceBracket
}) {
  const align: 'left' | 'right' = side === 'east' ? 'left' : 'right'

  // Layout for one conference: R1 (4 series) -> R2 (2 series) -> Conf Final
  return (
    <div className="flex-1 min-w-0">
      <div className={`flex items-center gap-2 mb-3 ${align === 'right' ? 'justify-end' : ''}`}>
        {champion && align === 'left' && (
          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-primary dark:text-ice">
            <Flame className="w-3 h-3" />
            {champion}
          </span>
        )}
        <p className="text-xs font-bold uppercase tracking-wider text-text-primary">{title}</p>
        {champion && align === 'right' && (
          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-primary dark:text-ice">
            {champion}
            <Flame className="w-3 h-3" />
          </span>
        )}
      </div>
      <div className={`flex gap-3 items-stretch ${align === 'right' ? 'flex-row-reverse' : ''}`}>
        <RoundColumn title="Round 1" series={bracket.round_1} align={align} count={4} />
        <RoundColumn title="Round 2" series={bracket.round_2} align={align} count={2} />
        <div className="flex flex-col flex-1 min-w-0">
          <p
            className={`text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-2 ${
              align === 'right' ? 'text-right' : ''
            }`}
          >
            Conf. Final
          </p>
          <div className="flex flex-col justify-around gap-2 h-full min-h-[160px]">
            <SeriesNode s={bracket.conference_final} align={align} />
          </div>
        </div>
      </div>
    </div>
  )
}

export function BracketDiagram({ championship, east, west }: Props) {
  const eastChamp = east.champion
  const westChamp = west.champion

  return (
    <motion.div
      initial={{ y: 12, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.35 }}
      className="w-full rounded-2xl border border-border bg-surface shadow-card p-4 md:p-5"
    >
      <div className="flex items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2">
          <Trophy className="w-4 h-4 text-amber-500" />
          <p className="text-sm font-bold uppercase tracking-wider text-text-primary">
            Tournament Bracket
          </p>
        </div>
        <p className="text-[10px] text-text-muted hidden md:block">
          {ROUND_TITLES.join(' · ')}
        </p>
      </div>

      {/* Desktop: full bracket with championship in the middle */}
      <div className="hidden lg:grid lg:grid-cols-[1fr_auto_1fr] gap-4 items-center">
        <ConferenceColumn side="east" title="Eastern Conference" champion={eastChamp} bracket={east} />
        <div className="flex flex-col items-center justify-center min-w-[170px] px-2">
          <ChampionshipNode s={championship} eastChamp={eastChamp} westChamp={westChamp} />
        </div>
        <ConferenceColumn side="west" title="Western Conference" champion={westChamp} bracket={west} />
      </div>

      {/* Tablet: stack East -> Championship -> West */}
      <div className="hidden md:flex lg:hidden flex-col gap-4">
        <ConferenceColumn side="east" title="Eastern Conference" champion={eastChamp} bracket={east} />
        <div className="self-center">
          <ChampionshipNode s={championship} eastChamp={eastChamp} westChamp={westChamp} />
        </div>
        <ConferenceColumn side="west" title="Western Conference" champion={westChamp} bracket={west} />
      </div>

      {/* Mobile: collapsed view with the most-relevant slice first */}
      <div className="md:hidden space-y-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2">
            Eastern Conference{eastChamp && (
              <span className="ml-1 text-primary dark:text-ice">· {eastChamp}</span>
            )}
          </p>
          <MobileBracketSlice bracket={east} />
        </div>
        <div className="flex justify-center">
          <ChampionshipNode s={championship} eastChamp={eastChamp} westChamp={westChamp} />
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-text-primary mb-2">
            Western Conference{westChamp && (
              <span className="ml-1 text-primary dark:text-ice">· {westChamp}</span>
            )}
          </p>
          <MobileBracketSlice bracket={west} />
        </div>
      </div>
    </motion.div>
  )
}

/**
 * Mobile collapsed bracket: shows Conf Final + R2 + R1 vertically. We
 * lead with the latest round so a fan opening the page on a phone sees
 * the most-recent matchup at the top instead of having to scroll past
 * round 1 to find the live series.
 */
function MobileBracketSlice({ bracket }: { bracket: ConferenceBracket }) {
  return (
    <div className="space-y-2">
      {bracket.conference_final && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">
            Conference Final
          </p>
          <SeriesNode s={bracket.conference_final} />
        </div>
      )}
      {bracket.round_2.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">
            Round 2
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {bracket.round_2.map((s, i) => (
              <SeriesNode key={`${s.team_a}-${s.team_b}-${i}`} s={s} />
            ))}
          </div>
        </div>
      )}
      {bracket.round_1.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">
            Round 1
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {bracket.round_1.map((s, i) => (
              <SeriesNode key={`${s.team_a}-${s.team_b}-${i}`} s={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
