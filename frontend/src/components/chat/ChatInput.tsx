'use client'

import { useState, useRef, KeyboardEvent, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Send, Zap, Paperclip, X } from 'lucide-react'
import clsx from 'clsx'
import { AttachedFile } from '@/components/chat/ChatMessage'

// Accepted file types for the file picker
const ACCEPTED_TYPES = 'image/png,image/jpeg,image/gif,image/webp,application/pdf,text/csv'
const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10 MB

interface ChatInputProps {
  onSend: (message: string, files?: AttachedFile[]) => void
  isLoading?: boolean
  placeholder?: string
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

export function ChatInput({ onSend, isLoading, placeholder = 'Ask about NHL stats, players, or analytics...' }: ChatInputProps) {
  const [value, setValue] = useState('')
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = () => {
    const hasContent = value.trim() || attachments.length > 0
    if (hasContent && !isLoading) {
      onSend(value.trim(), attachments.length > 0 ? attachments : undefined)
      setValue('')
      setAttachments([])
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
    const target = e.target
    target.style.height = 'auto'
    target.style.height = `${Math.min(target.scrollHeight, 200)}px`
  }

  const addFiles = useCallback(async (files: File[]) => {
    const valid = files.filter(f => f.size <= MAX_FILE_SIZE)
    const newAttachments: AttachedFile[] = await Promise.all(
      valid.map(async (file) => ({
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        name: file.name,
        type: file.type,
        dataUrl: await readFileAsDataUrl(file),
        size: file.size,
      }))
    )
    setAttachments(prev => [...prev, ...newAttachments])
  }, [])

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(Array.from(e.target.files))
      e.target.value = '' // reset so same file can be re-picked
    }
  }

  const handlePaste = useCallback(async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const imageItems = Array.from(e.clipboardData.items).filter(
      item => item.kind === 'file' && item.type.startsWith('image/')
    )
    if (imageItems.length > 0) {
      e.preventDefault()
      const files = imageItems.map(item => item.getAsFile()).filter(Boolean) as File[]
      addFiles(files)
    }
    // If no images, let normal text paste proceed
  }, [addFiles])

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  const canSend = (value.trim() || attachments.length > 0) && !isLoading

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative"
    >
      {/* Subtle glow effect */}
      <div className="absolute -inset-1 bg-gradient-to-r from-primary/10 via-ice/10 to-primary/10 rounded-2xl blur-xl opacity-50" />

      <div className="relative bg-surface border-2 border-border hover:border-primary/30 focus-within:border-primary/50 rounded-2xl overflow-hidden shadow-soft transition-all duration-200">

        {/* Attachment previews */}
        <AnimatePresence initial={false}>
          {attachments.length > 0 && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
              className="flex flex-wrap gap-2 px-3 pt-3"
            >
              {attachments.map((file) => (
                <motion.div
                  key={file.id}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.8 }}
                  transition={{ duration: 0.15 }}
                  className="relative group"
                >
                  {file.type.startsWith('image/') ? (
                    <img
                      src={file.dataUrl}
                      alt={file.name}
                      className="h-16 w-16 object-cover rounded-lg border border-border"
                    />
                  ) : (
                    <div className="h-16 w-24 flex flex-col items-center justify-center gap-1 rounded-lg border border-border bg-surface-elevated text-center px-2">
                      <span className="text-xl">📎</span>
                      <span className="text-[10px] text-text-muted leading-tight truncate w-full text-center">
                        {file.name}
                      </span>
                    </div>
                  )}
                  {/* Remove button */}
                  <button
                    onClick={() => removeAttachment(file.id)}
                    className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-text-primary text-background flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
                    aria-label="Remove attachment"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

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
            onPaste={handlePaste}
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

          {/* Attach file button */}
          <motion.button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
            className={clsx(
              'flex-shrink-0 p-2.5 rounded-xl transition-all duration-200',
              'text-text-muted hover:text-primary hover:bg-primary/5',
              isLoading && 'opacity-40 pointer-events-none'
            )}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            title="Attach image or file"
            aria-label="Attach file"
          >
            <Paperclip className="w-5 h-5" />
          </motion.button>

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            multiple
            onChange={handleFileInput}
            className="hidden"
            aria-hidden="true"
          />

          {/* Send button */}
          <motion.button
            onClick={handleSubmit}
            disabled={!canSend}
            className={clsx(
              'flex-shrink-0 p-3 rounded-xl transition-all duration-200',
              canSend
                ? 'bg-primary text-white hover:bg-primary-light shadow-nhl'
                : 'bg-surface-elevated text-text-muted border border-border'
            )}
            whileHover={canSend ? { scale: 1.05 } : undefined}
            whileTap={canSend ? { scale: 0.95 } : undefined}
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
            Press Enter to send · Shift+Enter for new line · Paste images directly
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
