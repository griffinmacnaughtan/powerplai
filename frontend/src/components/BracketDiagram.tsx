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
 * MobileVerticalBracket — the classic NHL playoff bracket pyramid,
 * rotated 90° so it converges from top to bottom on phone-class
 * viewports. Designed for iPhone 13 (390px) and up.
 *
 * Layout:
 *
 *   ┌─┐ ┌─┐ ┌─┐ ┌─┐   ← East R1 (4 series, wide row)
 *    └┐ ┌┘   └┐ ┌┘
 *     ┌┐     ┌┐
 *    ┌─┐    ┌─┐         ← East R2 (2 series)
 *      └┐  ┌┘
 *       ┌┐
 *       ┌─┐             ← East Conference Final (1 series)
 *        │
 *       ┌─┐             ← STANLEY CUP FINAL  (championship)
 *        │
 *       ┌─┐             ← West Conference Final
 *       ┌┐
 *      ┌┘  └┐
 *    ┌─┐    ┌─┐         ← West R2 (2 series)
 *     ┌┐     ┌┐
 *    ┌┘ └┐  ┌┘ └┐
 *   ┌─┐ ┌─┐ ┌─┐ ┌─┐   ← West R1 (4 series)
 *
 * The pyramid converges to the Stanley Cup Final in the middle, with
 * East flowing in from the top and West flowing in from the bottom.
 * SVG-rendered connectors stretch with the container so the lines
 * always meet the box centers exactly — no fragile margin math.
 *
 * Why these breakpoints
 * - Outer container is `md:hidden` (< 768px) so this view fires on all
 *   phone widths and small portrait tablets. iPad and desktop keep
 *   their existing 3-column layouts.
 * - R1 uses 4 narrow CompactSeriesNode boxes (~78px each on iPhone 13).
 * - R2 / CF / SCF use full-size SeriesNode / ChampionshipNode cards.
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
  const padded = (arr: (BracketSeries | null | undefined)[], n: number): (BracketSeries | null)[] =>
    Array.from({ length: n }, (_, i) => arr[i] ?? null)

  const eastR1 = padded(east.round_1, 4)
  const westR1 = padded(west.round_1, 4)
  const eastR2 = padded(east.round_2, 2)
  const westR2 = padded(west.round_2, 2)
  const eastCF = east.conference_final
  const westCF = west.conference_final

  return (
    <div className="flex flex-col items-stretch">
      {/* East Round 1 - 4 boxes spread across the full width */}
      <BracketRow count={4}>
        {eastR1.map((s, i) => (
          <CompactSeriesNode key={`er1-${i}`} s={s} onOpen={onOpen} />
        ))}
      </BracketRow>
      <BracketConnector from={4} to={2} direction="down" />

      {/* East Round 2 - 2 boxes */}
      <BracketRow count={2}>
        {eastR2.map((s, i) => (
          <SeriesNode key={`er2-${i}`} s={s} onOpen={onOpen} />
        ))}
      </BracketRow>
      <BracketConnector from={2} to={1} direction="down" />

      {/* East Conference Final - centered */}
      <BracketRow count={1} centered>
        <ConfFinalCard s={eastCF} side="east" onOpen={onOpen} />
      </BracketRow>
      <BracketConnector from={1} to={1} direction="down" />

      {/* STANLEY CUP FINAL - centered, featured */}
      <div className="flex justify-center my-1">
        <ChampionshipNode
          s={championship}
          eastChamp={eastChamp}
          westChamp={westChamp}
          onOpen={onOpen}
        />
      </div>

      {/* West Conference Final - centered */}
      <BracketConnector from={1} to={1} direction="down" />
      <BracketRow count={1} centered>
        <ConfFinalCard s={westCF} side="west" onOpen={onOpen} />
      </BracketRow>

      {/* West Round 2 - 2 boxes */}
      <BracketConnector from={1} to={2} direction="down" />
      <BracketRow count={2}>
        {westR2.map((s, i) => (
          <SeriesNode key={`wr2-${i}`} s={s} onOpen={onOpen} />
        ))}
      </BracketRow>

      {/* West Round 1 - 4 boxes */}
      <BracketConnector from={2} to={4} direction="down" />
      <BracketRow count={4}>
        {westR1.map((s, i) => (
          <CompactSeriesNode key={`wr1-${i}`} s={s} onOpen={onOpen} />
        ))}
      </BracketRow>
    </div>
  )
}

/**
 * One horizontal row in the bracket: N evenly-spaced cells. The
 * children should be exactly `count` items.
 *
 * To get the bracket connector lines to land on box centres exactly,
 * we use a no-gap grid and put the visual spacing inside each cell
 * (`px-[3px]`). With `gap`, cells would be (containerWidth - totalGap)
 * / N wide and their centres would land at e.g. 11.87% / 37.29% / ...
 * instead of the ideal 12.5% / 37.5% / ... that the SVG paths assume.
 * Cell padding keeps cells at exactly 1/N of the row, so the centres
 * align with the connectors regardless of viewport width.
 */
function BracketRow({
  count,
  centered = false,
  children,
}: {
  count: number
  centered?: boolean
  children: React.ReactNode
}) {
  if (centered && count === 1) {
    return (
      <div className="flex justify-center">
        <div className="w-[55%] min-w-[150px]">{children}</div>
      </div>
    )
  }
  const items = Array.isArray(children) ? children : [children]
  return (
    <div
      className="grid"
      style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
    >
      {items.map((child, i) => (
        <div key={i} className="px-[3px] min-w-0">
          {child}
        </div>
      ))}
    </div>
  )
}

/**
 * SVG bracket connector between two rounds. `from` and `to` are the
 * number of boxes in the row above and below, respectively. The SVG
 * uses preserveAspectRatio="none" so it stretches with its container,
 * meaning the paths (defined in % units within a 100×100 viewBox) line
 * up with the grid columns above and below automatically.
 *
 * Box centres for N-column grid: i = 0..N-1 → ((i + 0.5) / N) × 100%
 *   N=4 → 12.5, 37.5, 62.5, 87.5
 *   N=2 → 25, 75
 *   N=1 → 50
 */
function BracketConnector({
  from,
  to,
  direction = 'down',
  height = 22,
}: {
  from: number
  to: number
  /** Visual direction the connector flows (down for East side, mirror for West) */
  direction?: 'down' | 'up'
  height?: number
}) {
  const centersFor = (n: number) =>
    Array.from({ length: n }, (_, i) => ((i + 0.5) / n) * 100)

  const topCenters = centersFor(from)
  const botCenters = centersFor(to)

  // Build a list of SVG paths that connect the box centres above to the
  // box centres below. The "merge" or "split" always happens at the
  // vertical midpoint of the connector.
  const paths: string[] = []
  const mid = 50

  // Vertical drops from each top centre to the midpoint
  for (const x of topCenters) {
    paths.push(`M ${x} 0 V ${mid}`)
  }
  // Vertical drops from the midpoint to each bottom centre
  for (const x of botCenters) {
    paths.push(`M ${x} ${mid} V 100`)
  }
  // Horizontal merges (or splits) at the midpoint
  if (from > to && to > 0) {
    // Converging: every `from/to` top centres merge into one bottom centre
    const ratio = from / to
    for (let j = 0; j < to; j++) {
      const groupStart = topCenters[Math.floor(j * ratio)]
      const groupEnd = topCenters[Math.floor((j + 1) * ratio) - 1]
      if (groupStart != null && groupEnd != null && groupStart !== groupEnd) {
        paths.push(`M ${groupStart} ${mid} H ${groupEnd}`)
      }
    }
  } else if (to > from && from > 0) {
    // Diverging: each top centre splits into multiple bottom centres
    const ratio = to / from
    for (let i = 0; i < from; i++) {
      const groupStart = botCenters[Math.floor(i * ratio)]
      const groupEnd = botCenters[Math.floor((i + 1) * ratio) - 1]
      if (groupStart != null && groupEnd != null && groupStart !== groupEnd) {
        paths.push(`M ${groupStart} ${mid} H ${groupEnd}`)
      }
    }
  }
  // from === to: single straight line, already drawn by the vertical
  // segments above.

  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className="w-full text-border"
      style={{ height: `${height}px`, transform: direction === 'up' ? 'scaleY(-1)' : undefined }}
      aria-hidden="true"
    >
      <g
        stroke="currentColor"
        strokeWidth="1.2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      >
        {paths.map((d, i) => (
          <path key={i} d={d} />
        ))}
      </g>
    </svg>
  )
}

/**
 * Compact series card for the wide R1 rows where each cell is only
 * ~78px wide on iPhone 13. Drops the absolute-positioned status pill
 * (which would overflow) and uses a tiny inline live dot instead.
 */
function CompactSeriesNode({
  s,
  onOpen,
}: {
  s: BracketSeries | null
  onOpen?: OpenSeriesFn
}) {
  if (!s) {
    return (
      <div className="rounded-md border border-dashed border-border/40 bg-background/30 dark:bg-surface/30 px-1.5 py-1 text-center">
        <p className="text-[9px] italic text-text-muted">TBD</p>
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
  const isLive = s.status === 'in_progress'

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      whileTap={{ scale: 0.97 }}
      onClick={() => onOpen?.(s)}
      aria-label={`Open details for ${s.team_a} vs ${s.team_b}`}
      className={`relative w-full min-w-0 rounded-md border text-left transition hover:border-primary/60 dark:hover:border-ice/60 hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
        isLive
          ? 'border-red-400/60 bg-red-500/5'
          : completed
            ? 'border-border/60 bg-background/60 dark:bg-surface/60'
            : 'border-border bg-surface'
      } px-1.5 py-1`}
    >
      {isLive && (
        <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
      )}
      <div className="flex items-center justify-between gap-1 leading-none">
        <span
          className={`text-[10px] font-bold truncate ${
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
        <span className="text-[10px] font-mono tabular-nums text-text-secondary">
          {s.team_a_wins}
        </span>
      </div>
      <div className="flex items-center justify-between gap-1 leading-none mt-0.5">
        <span
          className={`text-[10px] font-bold truncate ${
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
        <span className="text-[10px] font-mono tabular-nums text-text-secondary">
          {s.team_b_wins}
        </span>
      </div>
    </motion.button>
  )
}

/**
 * Conference Final card — slightly emphasised (subtle ring + label)
 * because this is the last step before the championship, and visually
 * the apex of each conference's pyramid.
 */
function ConfFinalCard({
  s,
  side,
  onOpen,
}: {
  s: BracketSeries | null
  side: 'east' | 'west'
  onOpen?: OpenSeriesFn
}) {
  return (
    <div className="relative">
      <p className="text-[9px] font-bold uppercase tracking-wider text-text-muted text-center mb-1">
        {side === 'east' ? 'Eastern' : 'Western'} Conference Final
      </p>
      <SeriesNode s={s} onOpen={onOpen} />
    </div>
  )
}
