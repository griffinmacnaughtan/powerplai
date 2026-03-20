'use client'

import { motion } from 'framer-motion'
import { User, Bot } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import clsx from 'clsx'
import { FeedbackButtons } from '@/components/chat/FeedbackButtons'

export interface AttachedFile {
  id: string
  name: string
  type: string    // MIME type
  dataUrl: string // full data-URI for display
  size: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Array<{ type: string; data: string }>
  queryType?: string
  timestamp: Date
  attachments?: AttachedFile[]
}

interface ChatMessageProps {
  message: Message
  isLatest?: boolean
}

export function ChatMessage({ message, isLatest }: ChatMessageProps) {
  const isUser = message.role === 'user'

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
      className={clsx(
        'flex gap-4 w-full',
        isUser ? 'justify-end' : 'justify-start'
      )}
    >
      {/* Avatar for assistant */}
      {!isUser && (
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ delay: 0.1, type: 'spring', stiffness: 200 }}
          className="flex-shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-primary-dark flex items-center justify-center shadow-nhl"
        >
          <motion.div
            animate={{
              y: [0, -2, 0],
            }}
            transition={{
              duration: 2,
              repeat: Infinity,
              ease: 'easeInOut'
            }}
          >
            <Bot className="w-5 h-5 text-white" />
          </motion.div>
        </motion.div>
      )}

      {/* Message bubble */}
      <div
        className={clsx(
          'max-w-[80%] rounded-2xl px-5 py-4 shadow-card',
          isUser
            ? 'bg-primary text-white rounded-br-md'
            : 'bg-surface border border-border rounded-bl-md'
        )}
      >
        {isUser ? (
          <div>
            {/* Attached images */}
            {message.attachments && message.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {message.attachments.map((file) =>
                  file.type.startsWith('image/') ? (
                    <img
                      key={file.id}
                      src={file.dataUrl}
                      alt={file.name}
                      className="max-h-40 max-w-[200px] rounded-lg object-cover border border-white/20"
                    />
                  ) : (
                    <div
                      key={file.id}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/10 text-xs text-white/80"
                    >
                      <span>📎</span>
                      <span className="max-w-[120px] truncate">{file.name}</span>
                    </div>
                  )
                )}
              </div>
            )}
            {message.content && (
              <p className="text-sm leading-relaxed">{message.content}</p>
            )}
          </div>
        ) : (
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        )}

        {/* Sources badge */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="mt-4 pt-3 border-t border-border"
          >
            <div className="flex flex-wrap gap-2">
              {message.sources.map((source, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-primary-50 dark:bg-primary/20 text-primary dark:text-ice font-medium"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-primary dark:bg-ice" />
                  {source.data === 'league_leaders' ? 'Stats Database' :
                   source.data === 'team_stats' ? 'Team Stats' :
                   source.data === 'all_teams_breakdown' ? 'All Teams' :
                   source.data === 'daily_briefing' ? 'Daily Briefing' :
                   source.type.toUpperCase()}
                </span>
              ))}
            </div>
          </motion.div>
        )}

        {/* Feedback buttons - only on assistant messages */}
        {!isUser && (
          <FeedbackButtons
            queryType={message.queryType}
            responsePreview={message.content}
          />
        )}
      </div>

      {/* Avatar for user */}
      {isUser && (
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ delay: 0.1, type: 'spring', stiffness: 200 }}
          className="flex-shrink-0 w-10 h-10 rounded-xl bg-surface-elevated border border-border flex items-center justify-center"
        >
          <User className="w-5 h-5 text-text-secondary" />
        </motion.div>
      )}
    </motion.div>
  )
}
