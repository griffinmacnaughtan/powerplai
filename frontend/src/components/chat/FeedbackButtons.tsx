'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ThumbsUp, ThumbsDown, Check, X } from 'lucide-react'
import clsx from 'clsx'
import { api } from '@/lib/api'

const CATEGORIES = [
  { id: 'incorrect_stats', label: 'Incorrect stats' },
  { id: 'wrong_prediction', label: 'Wrong prediction' },
  { id: 'outdated_data', label: 'Outdated data' },
  { id: 'hallucinated_info', label: 'Hallucinated info' },
  { id: 'not_helpful', label: 'Not helpful' },
]

interface FeedbackButtonsProps {
  queryType?: string
  responsePreview?: string
}

export function FeedbackButtons({ queryType = '', responsePreview = '' }: FeedbackButtonsProps) {
  const [state, setState] = useState<'idle' | 'expanding' | 'submitted'>('idle')
  const [selected, setSelected] = useState<'thumbs_up' | 'thumbs_down' | null>(null)
  const [category, setCategory] = useState('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleThumbsUp = async () => {
    if (state !== 'idle') return
    setSelected('thumbs_up')
    setState('submitted')
    api.submitFeedback({
      feedback_type: 'thumbs_up',
      query_type: queryType,
      response_preview: responsePreview.slice(0, 300),
    }).catch(() => {})
  }

  const handleThumbsDown = () => {
    if (state !== 'idle') return
    setSelected('thumbs_down')
    setState('expanding')
  }

  const handleSubmitNegative = async () => {
    setSubmitting(true)
    try {
      await api.submitFeedback({
        feedback_type: 'thumbs_down',
        query_type: queryType,
        category,
        comment,
        response_preview: responsePreview.slice(0, 300),
      })
    } catch { /* fail silently */ }
    setSubmitting(false)
    setState('submitted')
  }

  const handleDismiss = () => {
    setState('idle')
    setSelected(null)
    setCategory('')
    setComment('')
  }

  if (state === 'submitted') {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="flex items-center gap-1.5 text-xs text-text-muted mt-2"
      >
        <Check className="w-3.5 h-3.5 text-green-500" />
        <span>{selected === 'thumbs_up' ? 'Thanks for the feedback!' : 'Feedback submitted — we\'ll improve.'}</span>
      </motion.div>
    )
  }

  return (
    <div className="mt-2">
      {/* Thumbs row */}
      <div className="flex items-center gap-1">
        <motion.button
          onClick={handleThumbsUp}
          disabled={state !== 'idle'}
          className={clsx(
            'p-1.5 rounded-lg transition-colors',
            selected === 'thumbs_up'
              ? 'text-green-500 bg-green-50 dark:bg-green-950'
              : 'text-text-muted hover:text-green-500 hover:bg-green-50 dark:hover:bg-green-950'
          )}
          whileHover={{ scale: 1.15 }}
          whileTap={{ scale: 0.9 }}
          title="Helpful"
          aria-label="Mark as helpful"
        >
          <ThumbsUp className="w-3.5 h-3.5" />
        </motion.button>

        <motion.button
          onClick={handleThumbsDown}
          disabled={state !== 'idle'}
          className={clsx(
            'p-1.5 rounded-lg transition-colors',
            selected === 'thumbs_down'
              ? 'text-accent bg-accent/10'
              : 'text-text-muted hover:text-accent hover:bg-accent/10'
          )}
          whileHover={{ scale: 1.15 }}
          whileTap={{ scale: 0.9 }}
          title="Not helpful"
          aria-label="Mark as not helpful"
        >
          <ThumbsDown className="w-3.5 h-3.5" />
        </motion.button>
      </div>

      {/* Expanded feedback form */}
      <AnimatePresence>
        {state === 'expanding' && (
          <motion.div
            initial={{ opacity: 0, height: 0, y: -8 }}
            animate={{ opacity: 1, height: 'auto', y: 0 }}
            exit={{ opacity: 0, height: 0, y: -8 }}
            transition={{ duration: 0.2 }}
            className="mt-2 p-3 rounded-xl border border-border bg-surface shadow-card overflow-hidden max-w-sm"
          >
            {/* Category chips */}
            <p className="text-xs font-medium text-text-secondary mb-2">What went wrong?</p>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {CATEGORIES.map(cat => (
                <button
                  key={cat.id}
                  onClick={() => setCategory(prev => prev === cat.id ? '' : cat.id)}
                  className={clsx(
                    'px-2.5 py-1 rounded-full text-xs border transition-all',
                    category === cat.id
                      ? 'bg-accent/10 border-accent text-accent'
                      : 'border-border text-text-muted hover:border-accent/50 hover:text-text-secondary'
                  )}
                >
                  {cat.label}
                </button>
              ))}
            </div>

            {/* Optional comment */}
            <textarea
              value={comment}
              onChange={e => setComment(e.target.value)}
              placeholder="Add a note (optional)..."
              rows={2}
              maxLength={300}
              className="w-full text-xs bg-surface-elevated border border-border rounded-lg px-2.5 py-2 text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-primary/40 mb-2"
            />

            {/* Actions */}
            <div className="flex items-center justify-between">
              <button
                onClick={handleDismiss}
                className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
              >
                <X className="w-3 h-3" /> Cancel
              </button>
              <motion.button
                onClick={handleSubmitNegative}
                disabled={submitting}
                className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-medium hover:bg-primary-light transition-colors disabled:opacity-60"
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
              >
                {submitting ? 'Sending...' : 'Submit'}
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
