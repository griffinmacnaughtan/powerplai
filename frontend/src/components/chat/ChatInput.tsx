'use client'

import { useState, useRef, KeyboardEvent } from 'react'
import { motion } from 'framer-motion'
import { Send, Zap } from 'lucide-react'
import clsx from 'clsx'

interface ChatInputProps {
  onSend: (message: string) => void
  isLoading?: boolean
  placeholder?: string
}

export function ChatInput({ onSend, isLoading, placeholder = 'Ask about NHL stats, players, or analytics...' }: ChatInputProps) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = () => {
    if (value.trim() && !isLoading) {
      onSend(value.trim())
      setValue('')
      // Reset textarea height
      if (inputRef.current) {
        inputRef.current.style.height = 'auto'
      }
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
    // Auto-resize textarea
    const target = e.target
    target.style.height = 'auto'
    target.style.height = `${Math.min(target.scrollHeight, 200)}px`
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative"
    >
      {/* Subtle glow effect */}
      <div className="absolute -inset-1 bg-gradient-to-r from-primary/10 via-ice/10 to-primary/10 rounded-2xl blur-xl opacity-50" />

      <div className="relative bg-surface border-2 border-border hover:border-primary/30 focus-within:border-primary/50 rounded-2xl overflow-hidden shadow-soft transition-all duration-200">
        <div className="flex items-end gap-2 p-2">
          {/* AI indicator */}
          <div className="flex-shrink-0 p-2 text-primary">
            <Zap className="w-5 h-5" />
          </div>

          {/* Input */}
          <textarea
            ref={inputRef}
            value={value}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={isLoading}
            rows={1}
            className={clsx(
              'flex-1 bg-transparent text-text-primary placeholder:text-text-muted',
              'resize-none py-2 px-1 max-h-[200px]',
              'focus:outline-none',
              'text-[15px] leading-relaxed',
              isLoading && 'opacity-50'
            )}
          />

          {/* Send button */}
          <motion.button
            onClick={handleSubmit}
            disabled={!value.trim() || isLoading}
            className={clsx(
              'flex-shrink-0 p-3 rounded-xl transition-all duration-200',
              value.trim() && !isLoading
                ? 'bg-primary text-white hover:bg-primary-light shadow-nhl'
                : 'bg-surface-elevated text-text-muted border border-border'
            )}
            whileHover={value.trim() && !isLoading ? { scale: 1.05 } : undefined}
            whileTap={value.trim() && !isLoading ? { scale: 0.95 } : undefined}
          >
            {isLoading ? (
              <motion.div
                className="w-5 h-5 border-2 border-text-muted border-t-primary rounded-full"
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </motion.button>
        </div>

        {/* Character count / hint */}
        <div className="px-4 pb-2 flex items-center justify-between">
          <span className="text-xs text-text-muted">
            Press Enter to send, Shift+Enter for new line
          </span>
          {value.length > 0 && (
            <span className="text-xs text-text-muted">
              {value.length} / 1000
            </span>
          )}
        </div>
      </div>
    </motion.div>
  )
}
