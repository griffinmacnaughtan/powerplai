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
import { useState } from 'react'
import { motion } from 'framer-motion'
import { Trophy, Flame } from 'lucide-react'
import type { BracketSeries, ConferenceBracket } from '@/lib/api'
import { SeriesDetailModal } from './SeriesDetailModal'

type Props = {
  championship: BracketSeries | null
  east: ConferenceBracket
  west: ConferenceBracket
}

// Module-level state-bridge so all SeriesNode instances can open the
// shared modal without prop drilling through 4 levels.
type OpenSeriesFn = (s: BracketSeries) => void

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
  onOpen,
}: {
  s: BracketSeries | null
  align?: 'left' | 'right' | 'center'
  onOpen?: OpenSeriesFn
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

  const baseClasses = `relative w-full rounded-lg border ${
    s.status === 'in_progress'
      ? 'border-red-400/60 dark:border-red-500/50 bg-red-500/5 dark:bg-red-500/10'
      : completed
        ? 'border-border/60 bg-background/60 dark:bg-surface/60'
        : 'border-border bg-surface'
  } shadow-sm px-2.5 py-2 space-y-1`

  const interactiveClasses = onOpen
    ? 'cursor-pointer text-left transition-all hover:shadow-md hover:border-primary/60 dark:hover:border-ice/60 hover:-translate-y-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 dark:focus-visible:ring-ice/50'
    : ''

  const content = (
    <>
      <div className="absolute -top-2 right-2">
        <ScorePill s={s} />
      </div>
      {teamARow}
      {teamBRow}
    </>
  )

  if (onOpen) {
    return (
      <motion.button
        type="button"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18 }}
        whileTap={{ scale: 0.985 }}
        onClick={() => onOpen(s)}
        aria-label={`Open details for ${s.team_a} vs ${s.team_b}`}
        className={`${baseClasses} ${interactiveClasses}`}
      >
        {content}
      </motion.button>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={baseClasses}
    >
      {content}
    </motion.div>
  )
}

function ChampionshipNode({ s, eastChamp, westChamp, onOpen }: {
  s: BracketSeries | null
  eastChamp: string | null
  westChamp: string | null
  onOpen?: OpenSeriesFn
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
          <SeriesNode s={display} onOpen={s ? onOpen : undefined} />
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
  onOpen,
}: {
  title: string
  series: (BracketSeries | null)[]
  align: 'left' | 'right'
  count: number
  onOpen?: OpenSeriesFn
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
            <SeriesNode s={s} align={align} onOpen={onOpen} />
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
  onOpen,
}: {
  side: 'east' | 'west'
  title: string
  champion: string | null
  bracket: ConferenceBracket
  onOpen?: OpenSeriesFn
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
        <RoundColumn title="Round 1" series={bracket.round_1} align={align} count={4} onOpen={onOpen} />
        <RoundColumn title="Round 2" series={bracket.round_2} align={align} count={2} onOpen={onOpen} />
        <div className="flex flex-col flex-1 min-w-0">
          <p
            className={`text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-2 ${
              align === 'right' ? 'text-right' : ''
            }`}
          >
            Conf. Final
          </p>
          <div className="flex flex-col justify-around gap-2 h-full min-h-[160px]">
            <SeriesNode s={bracket.conference_final} align={align} onOpen={onOpen} />
          </div>
        </div>
      </div>
    </div>
  )
}

export function BracketDiagram({ championship, east, west }: Props) {
  const eastChamp = east.champion
  const westChamp = west.champion
  const [selected, setSelected] = useState<BracketSeries | null>(null)
  const onOpen: OpenSeriesFn = s => setSelected(s)

  return (
    <>
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
            Click a series for game-by-game detail
          </p>
        </div>

        {/* Desktop: full bracket with championship in the middle */}
        <div className="hidden lg:grid lg:grid-cols-[1fr_auto_1fr] gap-4 items-center">
          <ConferenceColumn side="east" title="Eastern Conference" champion={eastChamp} bracket={east} onOpen={onOpen} />
          <div className="flex flex-col items-center justify-center min-w-[170px] px-2">
            <ChampionshipNode s={championship} eastChamp={eastChamp} westChamp={westChamp} onOpen={onOpen} />
          </div>
          <ConferenceColumn side="west" title="Western Conference" champion={westChamp} bracket={west} onOpen={onOpen} />
        </div>

        {/* Tablet: stack East -> Championship -> West */}
        <div className="hidden md:flex lg:hidden flex-col gap-4">
          <ConferenceColumn side="east" title="Eastern Conference" champion={eastChamp} bracket={east} onOpen={onOpen} />
          <div className="self-center">
            <ChampionshipNode s={championship} eastChamp={eastChamp} westChamp={westChamp} onOpen={onOpen} />
          </div>
          <ConferenceColumn side="west" title="Western Conference" champion={westChamp} bracket={west} onOpen={onOpen} />
        </div>

        {/* Mobile: proper vertical tournament bracket (East | West, R1 -> SCF) */}
        <div className="md:hidden">
          <MobileVerticalBracket
            east={east}
            west={west}
            championship={championship}
            eastChamp={eastChamp}
            westChamp={westChamp}
            onOpen={onOpen}
          />
        </div>
      </motion.div>
      <SeriesDetailModal series={selected} onClose={() => setSelected(null)} />
    </>
  )
}

/**
 * MobileVerticalBracket — proper tournament tree for phone-class viewports
 * (designed for iPhone 13's 390px wide and up; falls back gracefully to
 * any narrower or wider mobile/small-tablet screen).
 *
 * Layout (top to bottom):
 *
 *                 ROUND 1
 *     ┌───────────┐    ┌───────────┐
 *     │ East R1   │    │ West R1   │   4 series each conference
 *     │ ...       │    │ ...       │
 *     └─────┬─────┘    └─────┬─────┘
 *           ▼                ▼
 *                 ROUND 2
 *     ┌───────────┐    ┌───────────┐
 *     │ East R2   │    │ West R2   │   2 series each conference
 *     └─────┬─────┘    └─────┬─────┘
 *           ▼                ▼
 *             CONFERENCE FINALS
 *     ┌───────────┐    ┌───────────┐
 *     │ East CF   │    │ West CF   │
 *     └─────┬─────┘    └─────┬─────┘
 *            ╲              ╱
 *             ╲            ╱
 *           STANLEY CUP FINAL
 *               ┌────────┐
 *               │   …    │
 *               └────────┘
 *
 * The 2-column East|West grid keeps the bracket recognisable as a
 * tournament tree on a narrow screen. Round dividers + small chevron
 * indicators between rounds give a sense of progression. The SCF is a
 * single full-width card at the bottom so it lands as the visual
 * climax of the scroll.
 *
 * Why these breakpoints
 * - Outer container is `md:hidden` (< 768px) so this view fires on all
 *   phone widths and small portrait tablets, while iPad-class screens
 *   and desktops keep the existing layouts.
 * - The 2-col grid uses `gap-2` for iPhone 13 (390px) and tightens up
 *   automatically: each column is ~165px, plenty for a series card
 *   ("MTL  4-3  TBL won" with badge).
 */
function MobileVerticalBracket({
  east,
  west,
  championship,
  eastChamp,
  westChamp,
  onOpen,
}: {
  east: ConferenceBracket
  west: ConferenceBracket
  championship: BracketSeries | null
  eastChamp: string | null
  westChamp: string | null
  onOpen?: OpenSeriesFn
}) {
  // Pad each round to its expected slot count so two conferences stay
  // aligned even when matchups are still TBD.
  const padded = <T extends BracketSeries | null>(arr: (T | undefined)[], n: number): (BracketSeries | null)[] =>
    Array.from({ length: n }, (_, i) => arr[i] ?? null)

  const eastR1 = padded(east.round_1, 4)
  const westR1 = padded(west.round_1, 4)
  const eastR2 = padded(east.round_2, 2)
  const westR2 = padded(west.round_2, 2)
  const eastCF = east.conference_final
  const westCF = west.conference_final

  return (
    <div className="space-y-3">
      {/* Conference column headers row */}
      <div className="grid grid-cols-2 gap-2 px-1">
        <p className="text-[10px] font-bold uppercase tracking-wider text-text-primary">
          East{eastChamp && (
            <span className="ml-1 text-primary dark:text-ice">· {eastChamp}</span>
          )}
        </p>
        <p className="text-[10px] font-bold uppercase tracking-wider text-text-primary text-right">
          {westChamp && (
            <span className="mr-1 text-primary dark:text-ice">{westChamp} ·</span>
          )}
          West
        </p>
      </div>

      {/* Round 1 */}
      <RoundBlock label="Round 1">
        <div className="grid grid-cols-2 gap-2">
          <ColumnStack series={eastR1} onOpen={onOpen} />
          <ColumnStack series={westR1} onOpen={onOpen} />
        </div>
      </RoundBlock>

      <RoundConnector />

      {/* Round 2 */}
      <RoundBlock label="Round 2">
        <div className="grid grid-cols-2 gap-2">
          <ColumnStack series={eastR2} onOpen={onOpen} />
          <ColumnStack series={westR2} onOpen={onOpen} />
        </div>
      </RoundBlock>

      <RoundConnector />

      {/* Conference Finals */}
      <RoundBlock label="Conference Finals">
        <div className="grid grid-cols-2 gap-2">
          <ColumnStack series={[eastCF]} onOpen={onOpen} />
          <ColumnStack series={[westCF]} onOpen={onOpen} />
        </div>
      </RoundBlock>

      {/* Connector tapering from two columns into one */}
      <div aria-hidden="true" className="relative h-5 flex items-center justify-center">
        <div className="absolute inset-x-[25%] top-0 h-3 border-t border-l border-r border-border/60 rounded-t-md" />
        <div className="absolute left-1/2 top-3 w-px h-2 bg-border/60" />
      </div>

      {/* Stanley Cup Final */}
      <div className="flex justify-center">
        <ChampionshipNode
          s={championship}
          eastChamp={eastChamp}
          westChamp={westChamp}
          onOpen={onOpen}
        />
      </div>
    </div>
  )
}

function RoundBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-1.5">
        <div className="flex-1 h-px bg-border/60" />
        <p className="text-[10px] font-bold uppercase tracking-wider text-text-muted px-1">
          {label}
        </p>
        <div className="flex-1 h-px bg-border/60" />
      </div>
      {children}
    </section>
  )
}

/** A vertical column of series cards (one per round per conference). */
function ColumnStack({
  series,
  onOpen,
}: {
  series: (BracketSeries | null)[]
  onOpen?: OpenSeriesFn
}) {
  return (
    <div className="space-y-1.5 min-w-0">
      {series.map((s, i) => (
        <SeriesNode key={i} s={s} onOpen={onOpen} />
      ))}
    </div>
  )
}

/** Decorative vertical-flow indicator between rounds. */
function RoundConnector() {
  return (
    <div aria-hidden="true" className="grid grid-cols-2 gap-2">
      <div className="flex justify-center">
        <div className="w-px h-3 bg-border/60" />
      </div>
      <div className="flex justify-center">
        <div className="w-px h-3 bg-border/60" />
      </div>
    </div>
  )
}
