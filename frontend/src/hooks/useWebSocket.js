import { useCallback, useEffect, useRef, useState } from 'react'

import { API_URL, getAccessToken } from '../api'

/**
 * WebSocket hook for live seat updates. Connects on mount, reconnects with
 * exponential backoff on drop, and fires the callbacks for seat/pricing messages.
 *
 * @param {number|null} eventId  null = do not connect
 * @param {(seat, action) => void} onSeatUpdate
 * @param {(pricing) => void} [onPricingUpdate]
 * @returns {{ status: 'connecting'|'open'|'closed' }}
 */
export function useWebSocket(eventId, onSeatUpdate, onPricingUpdate) {
  const [status, setStatus] = useState('connecting')

  const socketRef = useRef(null)
  const retryRef = useRef(0)
  const timerRef = useRef(null)
  // Track manual closure to prevent unnecessary reconnection attempts
  const closedByUsRef = useRef(false)

  // Store callbacks in refs to avoid re-triggering effects on every render
  const handlerRef = useRef(onSeatUpdate)
  handlerRef.current = onSeatUpdate

  const pricingRef = useRef(onPricingUpdate)
  pricingRef.current = onPricingUpdate

  const connect = useCallback(() => {
    if (!eventId) return

    // Abort if no token; server will reject unauthorized connections
    const token = getAccessToken()
    if (!token) return

    // Token goes via query param since the WebSocket API can't send custom headers
    const base = API_URL.replace(/^http/, 'ws')
    const wsUrl = `${base}/ws/events/${eventId}?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(wsUrl)
    socketRef.current = socket
    setStatus('connecting')

    socket.onopen = () => {
      setStatus('open')
      retryRef.current = 0      // Reset backoff on successful connection
    }

    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'seat_update') {
          handlerRef.current?.(msg.seat, msg.action)
        } else if (msg.type === 'pricing_update') {
          // Event-wide demand multiplier update
          pricingRef.current?.(msg.pricing)
        }
      } catch {
        // Ignore malformed messages
      }
    }

    socket.onclose = () => {
      setStatus('closed')
      if (closedByUsRef.current) return

      // Exponential backoff (1s, 2s, 4s... capped at 15s) to avoid hammering
      // the server with reconnects when it comes back up
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
      retryRef.current += 1
      timerRef.current = setTimeout(connect, delay)
    }

    socket.onerror = () => socket.close()   // Close triggers retry logic
  }, [eventId])

  useEffect(() => {
    closedByUsRef.current = false
    connect()

    return () => {
      // Cleanup to prevent duplicate sockets in React StrictMode
      closedByUsRef.current = true
      clearTimeout(timerRef.current)
      socketRef.current?.close()
    }
  }, [connect])

  return { status }
}
