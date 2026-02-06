'use client'

import { useState, useCallback, useRef } from 'react'
import { api, QueryResponse, ChatHistoryMessage } from '@/lib/api'
import { Message } from '@/components/chat/ChatMessage'

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Use ref to get current messages in callback without stale closure
  const messagesRef = useRef<Message[]>([])
  messagesRef.current = messages

  const sendMessage = useCallback(async (content: string) => {
    // Build conversation history from existing messages (before adding new user message)
    const history: ChatHistoryMessage[] = messagesRef.current.map((msg) => ({
      role: msg.role,
      content: msg.content,
    }))

    // Add user message
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMessage])
    setIsLoading(true)
    setError(null)

    try {
      // Send query with conversation history for context
      const response: QueryResponse = await api.query(content, true, history)

      // Add assistant message
      const assistantMessage: Message = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: response.response,
        sources: response.sources,
        queryType: response.query_type,
        timestamp: new Date(),
      }

      setMessages((prev) => [...prev, assistantMessage])
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An error occurred'
      setError(errorMessage)

      // Add error message as assistant response
      const errorResponse: Message = {
        id: `error-${Date.now()}`,
        role: 'assistant',
        content: `Sorry, I encountered an error: ${errorMessage}. Please make sure the backend server is running on port 8000.`,
        timestamp: new Date(),
      }

      setMessages((prev) => [...prev, errorResponse])
    } finally {
      setIsLoading(false)
    }
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
    setError(null)
  }, [])

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
  }
}
