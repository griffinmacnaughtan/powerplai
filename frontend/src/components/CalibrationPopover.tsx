'use client'

/**
 * Calibration popover.
 *
 * Anchored to the "Calibrated to actual hit rates" footer line in
 * LockedPicksCard. Opens on click and shows the live data behind that
 * claim: sample size, Brier score (with plain-English interpretation),
 * the data-driven confidence thresholds, and the per-bucket actual-
 * versus-expected hit rates so a fan can see for themselves whether
 * the model is well-calibrated.
 *
 * Design
 * - Lazy fetch: only hits /api/model/diagnostics when the user opens.
 * - Click-outside / Escape to close.
 * - Single render path; no portal needed since the popover is
 *   positioned absolutely within the parent footer (above + right-
 *   aligned), and the LockedPicksCard isn't inside any overflow:hidden
 *   ancestor.
 */
import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Info, TrendingUp, X, Sparkles } from 'lucide-react'
import { api, type ModelDiagnostics, type DiagnosticsCalibrationBucket } from '@/lib/api'

function BucketBar({ b }: { b: DiagnosticsCalibrationBucket }) {
  // Visualize actual vs expected. Width = max of the two, marker at expected,
  // bar at actual. Color-coded by how close they are.
  const expected = b.expected_rate
  const actual = b.actual_rate
  const err = b.error
  // Two-stop colour: green when within 5pp, amber up to 10pp, red beyond.
  const color =
    err < 0.05
      ? 'bg-green-500'
      : err < 0.10
        ? 'bg-amber-500'
        : 'bg-red-500'

  return (
    <div className="flex items-center gap-1.5 text-[10px]">
      <span className="w-12 text-text-secondary font-mono tabular-nums">{b.range}</span>
      <span className="w-6 text-text-muted text-right">{b.n}</span>
      <div className="relative flex-1 h-3 bg-border/40 rounded-sm overflow-hidden">
        {/* Expected rate marker (a vertical line) */}
        <div
          className="absolute top-0 bottom-0 w-px bg-text-muted/60"
          style={{ left: `${Math.min(expected * 100, 100)}%` }}
        />
        {/* Actual rate bar */}
        <div
          className={`absolute top-0 bottom-0 left-0 ${color} opacity-70`}
          style={{ width: `${Math.min(actual * 100, 100)}%` }}
        />
      </div>
      <span className="w-10 font-mono tabular-nums text-text-primary text-right">
        {Math.round(actual * 100)}%
      </span>
    </div>
  )
}

export function CalibrationPopover() {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<ModelDiagnostics | null>(null)
  const [loading, setLoading] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Lazy fetch on first open
  useEffect(() => {
    if (!open || data || loading) return
    setLoading(true)
    api.getModelDiagnostics().then(d => {
      setData(d)
      setLoading(false)
    })
  }, [open, data, loading])

  // Click-outside + Escape
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative inline-flex items-center">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-label="Show calibration details"
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-[10px] text-text-muted hover:text-text-primary underline decoration-dotted decoration-text-muted/60 underline-offset-2 transition-colors"
      >
        Calibrated to actual hit rates
        <Info className="w-2.5 h-2.5" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 4 }}
            transition={{ duration: 0.15 }}
            className="absolute bottom-full right-0 mb-2 w-[320px] rounded-xl border border-border bg-surface shadow-2xl p-4 z-50"
            role="dialog"
            aria-label="Model calibration details"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5 text-primary dark:text-ice" />
                <p className="text-xs font-bold uppercase tracking-wider text-text-primary">
                  Model calibration
                </p>
              </div>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-background/60 transition-colors"
              >
                <X className="w-3 h-3" />
              </button>
            </div>

            {loading && (
              <div className="py-6 text-center">
                <div className="inline-block w-4 h-4 rounded-full border-2 border-primary/30 border-t-primary dark:border-ice/30 dark:border-t-ice animate-spin mb-2" />
                <p className="text-[11px] text-text-muted">Loading model diagnostics…</p>
              </div>
            )}

            {!loading && !data && (
              <p className="text-[11px] text-text-muted py-3">
                Couldn&apos;t load calibration data right now.
              </p>
            )}

            {!loading && data && (
              <div className="space-y-3">
                <p className="text-[11px] text-text-secondary leading-relaxed">
                  Every published pick is logged before games. When games end, we
                  record the actual outcome and rebuild the calibration map. Raw
                  model probabilities are then corrected against historical hit
                  rates before display.
                </p>

                {/* Summary stats */}
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="rounded-lg bg-background/50 dark:bg-surface/50 border border-border/50 p-2">
                    <p className="text-[10px] uppercase tracking-wider text-text-muted">
                      Sample
                    </p>
                    <p className="font-bold text-text-primary font-mono tabular-nums">
                      {data.calibration.sample_size}
                    </p>
                    <p className="text-[10px] text-text-muted">
                      validated picks · last {data.calibration.window_days}d
                    </p>
                  </div>
                  <div className="rounded-lg bg-background/50 dark:bg-surface/50 border border-border/50 p-2">
                    <p className="text-[10px] uppercase tracking-wider text-text-muted">
                      Brier (goal)
                    </p>
                    <p className="font-bold text-text-primary font-mono tabular-nums">
                      {data.calibration.brier.goal != null
                        ? data.calibration.brier.goal.toFixed(3)
                        : '—'}
                    </p>
                    <p className="text-[10px] text-text-muted capitalize">
                      {data.calibration.brier.interpretation}
                    </p>
                  </div>
                </div>

                {/* Thresholds */}
                <div className="rounded-lg bg-background/50 dark:bg-surface/50 border border-border/50 p-2 text-[11px]">
                  <div className="flex items-center gap-1 mb-1">
                    <Sparkles className="w-2.5 h-2.5 text-primary dark:text-ice" />
                    <p className="text-[10px] uppercase tracking-wider text-text-muted">
                      Confidence cuts
                    </p>
                    <span
                      className={`ml-auto text-[9px] px-1.5 py-0.5 rounded-sm uppercase font-semibold ${
                        data.calibration.thresholds.is_fallback
                          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                          : 'bg-green-500/10 text-green-700 dark:text-green-400'
                      }`}
                    >
                      {data.calibration.thresholds.is_fallback
                        ? 'fallback'
                        : 'data-driven'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-1 font-mono tabular-nums">
                    <span>
                      High ≥{' '}
                      <span className="text-text-primary font-bold">
                        {data.calibration.thresholds.high.toFixed(2)}
                      </span>
                    </span>
                    <span>
                      Medium ≥{' '}
                      <span className="text-text-primary font-bold">
                        {data.calibration.thresholds.medium.toFixed(2)}
                      </span>
                    </span>
                  </div>
                </div>

                {/* Per-bucket actual hit rates */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <p className="text-[10px] uppercase tracking-wider text-text-muted">
                      Goal-prob buckets · actual vs expected
                    </p>
                  </div>
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-1.5 text-[9px] text-text-muted uppercase tracking-wider">
                      <span className="w-12">Bucket</span>
                      <span className="w-6 text-right">n</span>
                      <span className="flex-1">Actual hit rate</span>
                      <span className="w-10 text-right">%</span>
                    </div>
                    {data.calibration.goal_buckets
                      .filter(b => b.n > 0)
                      .map(b => (
                        <BucketBar key={b.range} b={b} />
                      ))}
                  </div>
                  <p className="text-[10px] text-text-muted italic mt-1.5 leading-snug">
                    Green = within 5pp of expected · amber up to 10pp · red beyond.
                    Vertical line on each bar = expected rate.
                  </p>
                </div>

                {/* Footer note */}
                <p className="text-[10px] text-text-muted leading-snug">
                  Cache age: {Math.round(data.calibration.age_seconds || 0)}s ·
                  refreshes every 6 hours · season averages also calibrated:
                  league SV% {data.league_averages.save_pct.toFixed(3)},
                  G/Gm {data.league_averages.goals_per_game.toFixed(2)}.
                </p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
