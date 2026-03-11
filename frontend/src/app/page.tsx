'use client'

import { useRef, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Trash2, Github, Linkedin, Bot, TrendingUp, Trophy, Target, Sun, Moon, Newspaper } from 'lucide-react'
import { Logo, LogoText } from '@/components/Logo'
import { ChatMessage } from '@/components/chat/ChatMessage'
import { ChatInput } from '@/components/chat/ChatInput'
import { SuggestedQueries } from '@/components/chat/SuggestedQueries'
import { TypingIndicator } from '@/components/LoadingDots'
import { AccuracyBadge } from '@/components/AccuracyBadge'
import { ParlayTracker } from '@/components/ParlayTracker'
import { Button } from '@/components/ui'
import { useChat } from '@/hooks/useChat'

function useTheme() {
  const [dark, setDark] = useState(false)

  useEffect(() => {
    setDark(document.documentElement.classList.contains('dark'))
  }, [])

  const toggle = () => {
    const next = !dark
    setDark(next)
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('theme', next ? 'dark' : 'light')
  }

  return { dark, toggle }
}

export default function Home() {
  const { messages, isLoading, sendMessage, clearMessages } = useChat()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const { dark, toggle } = useTheme()

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const hasMessages = messages.length > 0

  const handleDailyBriefing = () => {
    sendMessage('daily briefing')
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* Header */}
      <header className="sticky top-0 z-50 glass border-b border-border dark:border-border/60">
        <div className="max-w-5xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={clearMessages} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
              <Logo size="sm" link={false} />
              <LogoText className="text-lg" />
            </button>
            <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-ice/15 dark:bg-ice/10 border border-ice/20 dark:border-ice/15 text-ice-dark dark:text-ice text-xs font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-ice dark:bg-ice animate-pulse" />
              LIVE
            </span>
            <AccuracyBadge />
          </div>

          <div className="flex items-center gap-2">
            {/* Daily Briefing button */}
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }}>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleDailyBriefing}
                disabled={isLoading}
                className="border-primary/30 dark:border-ice/25 text-primary dark:text-ice hover:bg-primary/5 dark:hover:bg-ice/8 hover:border-primary/60 dark:hover:border-ice/50 font-medium dark:bg-ice/5"
                title="Get today's injury alerts, goalies, top picks, and best bets"
              >
                <Newspaper className="w-4 h-4" />
                <span className="hidden sm:inline">Daily Briefing</span>
              </Button>
            </motion.div>

            <Button
              variant="ghost"
              size="sm"
              onClick={toggle}
              className="text-text-muted hover:text-text-primary dark:hover:text-ice dark:hover:bg-ice/10"
              title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </Button>
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
              className="border-border dark:border-border/70 hover:border-primary dark:hover:border-ice/40 hover:bg-primary/5 dark:hover:bg-ice/8 dark:text-text-secondary dark:hover:text-ice"
            >
              <Github className="w-4 h-4" />
              <span className="hidden sm:inline">GitHub</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => window.open('https://www.linkedin.com/in/griffin-macnaughtan/', '_blank')}
              className="border-border dark:border-border/70 hover:border-[#0A66C2] hover:bg-[#0A66C2]/5 dark:text-text-secondary dark:hover:text-[#5aabee] dark:hover:border-[#5aabee]/40"
            >
              <Linkedin className="w-4 h-4" />
              <span className="hidden sm:inline">LinkedIn</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 min-h-0 flex flex-col max-w-5xl mx-auto w-full px-4 py-6">
        <AnimatePresence mode="wait">
          {!hasMessages ? (
            // Welcome screen
            <motion.div
              key="welcome"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, y: -20 }}
              className="flex-1 min-h-0 overflow-y-auto flex flex-col py-6"
            >
              <div className="my-auto w-full flex flex-col items-center">
              <motion.h1
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.2 }}
                className="text-3xl sm:text-4xl font-bold text-center mb-2"
              >
                <span className="text-primary dark:text-ice">Your AI-Powered </span>
                <span className="gradient-text">Hockey Analyst</span>
              </motion.h1>

              <motion.p
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="text-text-secondary text-center max-w-lg mb-5 text-sm"
              >
                Ask questions about NHL stats, compare players, get fantasy advice,
                and explore analytics. Powered by real data and AI.
              </motion.p>

              {/* Daily Briefing hero card */}
              <motion.div
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.35 }}
                className="w-full max-w-sm mb-4"
              >
                <motion.button
                  onClick={handleDailyBriefing}
                  disabled={isLoading}
                  whileHover={{ scale: 1.02, y: -2 }}
                  whileTap={{ scale: 0.98 }}
                  className="w-full flex items-center gap-4 px-5 py-4 rounded-2xl bg-gradient-to-r from-primary/10 dark:from-ice/[0.08] via-primary/5 dark:via-ice/[0.04] to-transparent border border-primary/20 dark:border-ice/20 hover:border-primary/40 dark:hover:border-ice/40 shadow-card dark:shadow-none hover:shadow-soft dark:hover:shadow-[0_4px_20px_-4px_rgba(91,192,235,0.15)] transition-all text-left group"
                >
                  <div className="flex-shrink-0 w-10 h-10 rounded-xl bg-primary/10 dark:bg-ice/10 flex items-center justify-center group-hover:bg-primary/20 dark:group-hover:bg-ice/15 transition-colors">
                    <Newspaper className="w-5 h-5 text-primary dark:text-ice" />
                  </div>
                  <div>
                    <p className="font-semibold text-text-primary text-sm">Today's Daily Briefing</p>
                    <p className="text-xs text-text-muted mt-0.5">Injuries · Goalies · Top picks · Best bets</p>
                  </div>
                  <span className="ml-auto text-primary dark:text-ice opacity-0 group-hover:opacity-100 transition-opacity text-sm font-semibold">→</span>
                </motion.button>
              </motion.div>

              {/* Parlay tracker streak */}
              <ParlayTracker />

              {/* Feature badges */}
              <motion.div
                initial={{ y: 20, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.4 }}
                className="flex flex-wrap items-center justify-center gap-2 mb-5 mt-4"
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
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-surface dark:bg-surface/80 border border-border dark:border-border/70 shadow-card dark:shadow-none text-sm text-text-secondary dark:text-text-secondary hover:border-primary/30 dark:hover:border-ice/30 dark:hover:text-ice hover:shadow-soft transition-all cursor-help"
                  >
                    <feature.icon className="w-4 h-4 text-primary dark:text-ice" />
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
                <p className="text-xs text-text-muted mb-3 text-center font-medium">
                  Try one of these to get started
                </p>
                <SuggestedQueries onSelect={sendMessage} />
              </motion.div>

              {/* Powered by badge */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.8 }}
                className="mt-5 text-center"
              >
                <p className="text-xs text-text-muted">
                  Powered by MoneyPuck, NHL API, and Claude AI
                </p>
              </motion.div>
              </div>
            </motion.div>
          ) : (
            // Chat interface
            <motion.div
              key="chat"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex-1 min-h-0 flex flex-col"
            >
              {/* Messages */}
              <div className="flex-1 min-h-0 overflow-y-auto py-6 space-y-6">
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
