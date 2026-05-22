'use client'

/**
 * Tonight's picks, with prominent 2h-before-puck-drop "locked" treatment.
 *
 * Picks come in with a server-computed lock_status:
 *   open          -> game is more than 2h30m away; show as preliminary
 *   locking_soon  -> within 30m of locking; nudge user with countdown
 *   locked        -> within 2h of puck drop; the pick is "in"; show big
 *   live          -> game is in progress; show live indicator
 *   final         -> game is done; show graded result
 *
 * We rebroadcast the time-based bits client-side on a 30s ticker so the
 * countdown and status feel alive without needing to refetch the API.
 */
import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Lock, Target, Check, X, Radio, Hourglass } from 'lucide-react'
import type { PickLockStatus } from '@/lib/api'

export type LockedPick = {
  rank: number
  player_name: string
  team: string
  opponent: string
  is_home: boolean
  market: string
  line: string
  probability: number
  confidence: 'high' | 'medium' | 'low'
  hit?: boolean | null
  graded?: boolean
  start_time_utc?: string | null
  lock_time_utc?: string | null
  minutes_to_lock?: number | null
  minutes_to_puck_drop?: number | null
  lock_status?: PickLockStatus
  opponent_goalie?: string | null
}

type Props = {
  picks: LockedPick[]
  /** Only used if no pick has a lock time - falls back to "no games tonight" */
  fallbackNextGameDate?: string | null
}

/** Wall-clock tick to recompute countdowns. 30s is responsive without
 * being wasteful; user perception of "live" doesn't require sub-30s. */
function useTicker(intervalMs = 30_000) {
  const [, setN] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setN(n => n + 1), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
}

function formatCountdown(minutes: number | null | undefined): string {
  if (minutes == null) return ''
  if (minutes <= 0) return 'now'
  if (minutes < 1) return '<1m'
  if (minutes < 60) return `${Math.round(minutes)}m`
  const hours = Math.floor(minutes / 60)
  const mins = Math.round(minutes - hours * 60)
  if (hours < 24) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
  const days = Math.floor(hours / 24)
  const remHours = hours - days * 24
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`
}

function formatPuckDropTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/**
 * Recompute lock_status client-side based on the current time so a
 * stale fetch doesn't show "locking soon" for a pick that's actually
 * already locked. Falls back to server status when we don't have a
 * lock_time.
 */
function clientLockStatus(p: LockedPick): PickLockStatus {
  if (p.lock_status === 'final' || p.lock_status === 'live') return p.lock_status
  if (!p.lock_time_utc) return p.lock_status ?? 'open'

  const now = Date.now()
  const lockMs = new Date(p.lock_time_utc).getTime()
  const puckMs = p.start_time_utc ? new Date(p.start_time_utc).getTime() : Number.POSITIVE_INFINITY
  const minutesToLock = (lockMs - now) / 60_000
  const minutesToPuck = (puckMs - now) / 60_000

  if (minutesToPuck <= 0) return 'live'
  if (minutesToLock <= 0) return 'locked'
  if (minutesToLock <= 30) return 'locking_soon'
  return 'open'
}

function StatusBadge({ status }: { status: PickLockStatus }) {
  const config: Record<PickLockStatus, { label: string; icon: typeof Lock; classes: string }> = {
    open: {
      label: 'Preliminary',
      icon: Hourglass,
      classes: 'bg-border/20 text-text-muted',
    },
    locking_soon: {
      label: 'Locks soon',
      icon: Hourglass,
      classes: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 ring-1 ring-amber-500/30',
    },
    locked: {
      label: 'Locked in',
      icon: Lock,
      classes: 'bg-primary/10 dark:bg-ice/10 text-primary dark:text-ice ring-1 ring-primary/30 dark:ring-ice/30',
    },
    live: {
      label: 'Live',
      icon: Radio,
      classes: 'bg-red-500/10 text-red-600 dark:text-red-400 ring-1 ring-red-500/30',
    },
    final: {
      label: 'Final',
      icon: Check,
      classes: 'bg-green-500/10 text-green-700 dark:text-green-400 ring-1 ring-green-500/30',
    },
  }
  const C = config[status]
  const Icon = C.icon
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${C.classes}`}
    >
      <Icon className={`w-2.5 h-2.5 ${status === 'live' ? 'animate-pulse' : ''}`} />
      {C.label}
    </span>
  )
}

function PickRow({ pick }: { pick: LockedPick }) {
  const status = clientLockStatus(pick)
  const minutesToPuck = useMemo(() => {
    if (!pick.start_time_utc) return null
    return (new Date(pick.start_time_utc).getTime() - Date.now()) / 60_000
  }, [pick.start_time_utc])
  const minutesToLock = useMemo(() => {
    if (!pick.lock_time_utc) return null
    return (new Date(pick.lock_time_utc).getTime() - Date.now()) / 60_000
  }, [pick.lock_time_utc])

  const isLocked = status === 'locked' || status === 'live' || status === 'final'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className={`relative rounded-lg p-3 ${
        isLocked
          ? 'bg-background dark:bg-surface border-l-4 border-l-primary dark:border-l-ice border border-border'
          : 'bg-background/60 dark:bg-surface/60 border border-border/60'
      }`}
    >
      <div className="flex items-start gap-2">
        <span className="text-[10px] font-bold text-text-muted tabular-nums w-4 mt-0.5">
          {pick.rank}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <p className="text-sm font-bold text-text-primary truncate">{pick.player_name}</p>
            <StatusBadge status={status} />
            {pick.graded && pick.hit === true && (
              <Check className="w-4 h-4 text-green-500 flex-shrink-0" aria-label="Pick hit" role="img" />
            )}
            {pick.graded && pick.hit === false && (
              <X className="w-4 h-4 text-red-500 flex-shrink-0" aria-label="Pick missed" role="img" />
            )}
          </div>
          <p className="text-[11px] text-text-secondary mt-0.5">
            <span className="font-semibold">{pick.team}</span> {pick.is_home ? 'vs' : '@'}{' '}
            <span className="font-semibold">{pick.opponent}</span>{' '}
            · <span className="text-text-primary font-medium">{pick.market}</span>
            {pick.opponent_goalie && (
              <span className="text-text-muted"> vs {pick.opponent_goalie}</span>
            )}
          </p>
          <div className="mt-1 flex items-center gap-3 text-[11px]">
            <span className="font-mono tabular-nums font-bold text-primary dark:text-ice">
              {Math.round(pick.probability * 100)}%
            </span>
            <span className="text-[10px] uppercase tracking-wide text-text-muted">
              {pick.confidence}
            </span>
            {status === 'open' && minutesToLock != null && minutesToLock > 0 && (
              <span className="text-[10px] text-text-muted ml-auto">
                Locks {formatCountdown(minutesToLock)}
                {pick.start_time_utc && ` · puck ${formatPuckDropTime(pick.start_time_utc)}`}
              </span>
            )}
            {status === 'locking_soon' && (
              <span className="text-[10px] font-semibold text-amber-700 dark:text-amber-400 ml-auto">
                Locks in {formatCountdown(minutesToLock)}
              </span>
            )}
            {status === 'locked' && minutesToPuck != null && minutesToPuck > 0 && (
              <span className="text-[10px] font-semibold text-primary dark:text-ice ml-auto">
                Puck drop {formatCountdown(minutesToPuck)}
              </span>
            )}
            {status === 'live' && (
              <span className="text-[10px] font-semibold text-red-600 dark:text-red-400 ml-auto">
                In progress
              </span>
            )}
            {status === 'final' && pick.graded && (
              <span className="text-[10px] text-text-muted ml-auto">
                {pick.hit ? 'Hit' : 'Missed'}
              </span>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  )
}

export function LockedPicksCard({ picks, fallbackNextGameDate }: Props) {
  // Tick to drive countdown recomputation
  useTicker(30_000)

  // Sort: locked/live first (drama right now), then locking_soon, then open, then final
  const order: Record<PickLockStatus, number> = {
    locked: 0,
    live: 0,
    locking_soon: 1,
    open: 2,
    final: 3,
  }
  const sorted = useMemo(() => {
    return [...picks].sort((a, b) => {
      const sa = order[clientLockStatus(a)] ?? 4
      const sb = order[clientLockStatus(b)] ?? 4
      if (sa !== sb) return sa - sb
      return a.rank - b.rank
    })
  }, [picks])

  // Headline: the most-imminent status across all picks. Drives the badge
  // at the top of the card.
  const headline = useMemo(() => {
    if (sorted.length === 0) return null
    const statuses = sorted.map(clientLockStatus)
    if (statuses.includes('live')) return 'live'
    if (statuses.includes('locked')) return 'locked'
    if (statuses.includes('locking_soon')) return 'locking_soon'
    return statuses[0]
  }, [sorted])

  // Find the earliest unfulfilled lock time across the picks - used for
  // the "Locks in 1h 23m" header when nothing has hit the 2h window yet.
  const nextLockMinutes = useMemo(() => {
    const open = sorted.filter(p => clientLockStatus(p) === 'open')
    const upcoming = open
      .map(p =>
        p.lock_time_utc ? (new Date(p.lock_time_utc).getTime() - Date.now()) / 60_000 : null,
      )
      .filter((m): m is number => m != null && m > 0)
    if (upcoming.length === 0) return null
    return Math.min(...upcoming)
  }, [sorted])

  return (
    <motion.div
      initial={{ y: 12, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="w-full rounded-2xl border border-border bg-surface shadow-card p-4"
    >
      <div className="flex items-center gap-2 mb-3">
        <Target className="w-4 h-4 text-amber-500" />
        <p className="text-sm font-bold uppercase tracking-wider text-text-primary">
          Tonight&apos;s Picks
        </p>
        {headline === 'live' && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-red-600 dark:text-red-400">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            Live
          </span>
        )}
        {headline === 'locked' && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-primary dark:text-ice">
            <Lock className="w-2.5 h-2.5" />
            Locked in
          </span>
        )}
        {headline === 'locking_soon' && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-amber-700 dark:text-amber-400">
            <Hourglass className="w-2.5 h-2.5" />
            Locking soon
          </span>
        )}
        {headline === 'open' && nextLockMinutes != null && (
          <span className="ml-auto text-[10px] text-text-muted">
            Locks in {formatCountdown(nextLockMinutes)}
          </span>
        )}
      </div>

      <div className="space-y-2">
        <AnimatePresence initial={false}>
          {sorted.length > 0 ? (
            sorted.map(p => <PickRow key={p.rank} pick={p} />)
          ) : (
            <div className="rounded-lg border border-dashed border-border/60 px-3 py-4 text-center">
              <p className="text-xs text-text-muted italic">
                {fallbackNextGameDate
                  ? `Picks will lock in 2h before puck drop · next playoff game on ${fallbackNextGameDate}`
                  : 'No picks yet — they lock in 2 hours before puck drop on game days'}
              </p>
            </div>
          )}
        </AnimatePresence>
      </div>

      <p className="mt-3 text-[10px] text-text-muted text-center">
        Picks lock 2 hours before puck drop · model probabilities are calibrated to actual hit rates
      </p>
    </motion.div>
  )
}
