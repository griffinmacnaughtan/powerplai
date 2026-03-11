'use client'

import { useState, useCallback, useRef } from 'react'
import { api, QueryResponse, ChatHistoryMessage, ImageAttachment } from '@/lib/api'
import { Message, AttachedFile } from '@/components/chat/ChatMessage'
import { DEMO_MODE, getDemoResponse } from '@/lib/demoData'

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Use ref to get current messages in callback without stale closure
  const messagesRef = useRef<Message[]>([])
  messagesRef.current = messages

  const sendMessage = useCallback(async (content: string, files?: AttachedFile[]) => {
    // Build conversation history from existing messages (before adding new user message)
    const history: ChatHistoryMessage[] = messagesRef.current.map((msg) => ({
      role: msg.role,
      content: msg.content,
    }))

    // Add user message (with any attachments for display)
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date(),
      attachments: files,
    }

    setMessages((prev) => [...prev, userMessage])
    setIsLoading(true)
    setError(null)

    // Demo mode - return cached responses
    if (DEMO_MODE) {
      await new Promise(resolve => setTimeout(resolve, 800)) // Simulate API delay

      const demoResponse: Message = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: getDemoResponse(content),
        sources: [{ type: 'demo', data: 'Sample data for demonstration' }],
        queryType: 'demo',
        timestamp: new Date(),
      }

      setMessages((prev) => [...prev, demoResponse])
      setIsLoading(false)
      return
    }

    try {
      // Convert image attachments to the API format (strip data-URI prefix)
      const imageAttachments: ImageAttachment[] = (files ?? [])
        .filter(f => f.type.startsWith('image/'))
        .map(f => ({
          data: f.dataUrl.replace(/^data:[^;]+;base64,/, ''),
          media_type: f.type,
          name: f.name,
        }))

      // Send query with conversation history and optional images for context
      const response: QueryResponse = await api.query(content, true, history, imageAttachments)

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
        content: `Sorry, I encountered an error: ${errorMessage}`,
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
