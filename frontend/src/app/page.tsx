'use client'

import { useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Trash2, Github, Linkedin, Bot, TrendingUp, Trophy, Target } from 'lucide-react'
import { Logo, LogoText } from '@/components/Logo'
import { ChatMessage } from '@/components/chat/ChatMessage'
import { ChatInput } from '@/components/chat/ChatInput'
import { SuggestedQueries } from '@/components/chat/SuggestedQueries'
import { TypingIndicator } from '@/components/LoadingDots'
import { Button } from '@/components/ui'
import { useChat } from '@/hooks/useChat'

export default function Home() {
  const { messages, isLoading, sendMessage, clearMessages } = useChat()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const hasMessages = messages.length > 0

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-50 glass border-b border-border">
        <div className="max-w-5xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={clearMessages} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
              <Logo size="sm" link={false} />
              <LogoText className="text-lg" />
            </button>
            <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-ice/20 text-ice-dark text-xs font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-ice-dark animate-pulse" />
              LIVE
            </span>
          </div>

          <div className="flex items-center gap-2">
            {hasMessages && (
              <motion.div
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={clearMessages}
                  className="text-text-muted hover:text-accent hover:bg-accent/10"
                >
                  <Trash2 className="w-4 h-4" />
                  <span className="hidden sm:inline">Clear</span>
                </Button>
              </motion.div>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => window.open('https://github.com/griffinmacnaughtan/powerplai', '_blank')}
              className="border-border hover:border-primary hover:bg-primary/5"
            >
              <Github className="w-4 h-4" />
              <span className="hidden sm:inline">GitHub</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => window.open('https://www.linkedin.com/in/griffin-macnaughtan/', '_blank')}
              className="border-border hover:border-[#0A66C2] hover:bg-[#0A66C2]/5"
            >
              <Linkedin className="w-4 h-4" />
              <span className="hidden sm:inline">LinkedIn</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex flex-col max-w-5xl mx-auto w-full px-4 py-6">
        <AnimatePresence mode="wait">
          {!hasMessages ? (
            // Welcome screen
            <motion.div
              key="welcome"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, y: -20 }}
              className="flex-1 flex flex-col items-center justify-center py-12"
            >
              <motion.h1
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.2 }}
                className="text-4xl sm:text-5xl font-bold text-center mb-4"
              >
                <span className="text-primary">Your AI-Powered </span>
                <span className="gradient-text">Hockey Analyst</span>
              </motion.h1>

              <motion.p
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="text-text-secondary text-center max-w-lg mb-8 text-lg"
              >
                Ask questions about NHL stats, compare players, get fantasy advice,
                and explore analytics. Powered by real data and AI.
              </motion.p>

              {/* Feature badges */}
              <motion.div
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.4 }}
                className="flex flex-wrap items-center justify-center gap-3 mb-12"
              >
                {[
                  { icon: TrendingUp, label: 'Real-time Stats', tooltip: 'Live data from NHL API updated daily with game logs, standings, and player stats' },
                  { icon: Target, label: 'xG Analytics', tooltip: 'Expected goals, Corsi, Fenwick, and other advanced metrics from MoneyPuck' },
                  { icon: Trophy, label: 'Fantasy Insights', tooltip: 'Trade suggestions, value comparisons, and lineup advice for fantasy hockey' },
                  { icon: Bot, label: 'AI-Powered', tooltip: 'Claude AI analyzes stats and generates insights with natural language understanding' },
                ].map((feature, i) => (
                  <motion.span
                    key={feature.label}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.4 + i * 0.1 }}
                    title={feature.tooltip}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-surface border border-border shadow-card text-sm text-text-secondary hover:border-primary/30 hover:shadow-soft transition-all cursor-help"
                  >
                    <feature.icon className="w-4 h-4 text-primary" />
                    {feature.label}
                  </motion.span>
                ))}
              </motion.div>

              {/* Suggested queries */}
              <motion.div
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.5 }}
                className="w-full max-w-4xl"
              >
                <p className="text-sm text-text-muted mb-4 text-center font-medium">
                  Try one of these to get started
                </p>
                <SuggestedQueries onSelect={sendMessage} />
              </motion.div>

              {/* Powered by badge */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.8 }}
                className="mt-12 text-center"
              >
                <p className="text-xs text-text-muted">
                  Powered by MoneyPuck, NHL API, and Claude AI
                </p>
              </motion.div>
            </motion.div>
          ) : (
            // Chat interface
            <motion.div
              key="chat"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex-1 flex flex-col"
            >
              {/* Messages */}
              <div className="flex-1 overflow-y-auto py-6 space-y-6">
                <AnimatePresence initial={false}>
                  {messages.map((message) => (
                    <ChatMessage
                      key={message.id}
                      message={message}
                      isLatest={message.id === messages[messages.length - 1]?.id}
                    />
                  ))}
                </AnimatePresence>

                {/* Typing indicator */}
                {isLoading && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="flex gap-4"
                  >
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-primary-dark flex items-center justify-center shadow-nhl">
                      <motion.div
                        animate={{
                          y: [0, -3, 0],
                          rotate: [0, 5, -5, 0],
                        }}
                        transition={{
                          duration: 1.5,
                          repeat: Infinity,
                          ease: 'easeInOut'
                        }}
                      >
                        <Bot className="w-5 h-5 text-white" />
                      </motion.div>
                    </div>
                    <div className="bg-surface border border-border rounded-2xl rounded-bl-md px-5 py-4 shadow-card">
                      <TypingIndicator />
                    </div>
                  </motion.div>
                )}

                <div ref={messagesEndRef} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Input area - fixed at bottom */}
      <div className="sticky bottom-0 glass border-t border-border">
        <div className="max-w-5xl mx-auto px-4 py-4">
          <ChatInput onSend={sendMessage} isLoading={isLoading} />
        </div>
      </div>
    </div>
  )
}
