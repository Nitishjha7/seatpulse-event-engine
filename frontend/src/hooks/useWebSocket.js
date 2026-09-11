import { useCallback, useEffect, useRef, useState } from 'react'

import { API_URL, getAccessToken } from '../api'

/**
 * WebSocket hook for live event seat updates.
 *
 * Responsibilities:
 *   - Connect on mount, close on unmount
 *   - Reconnect with exponential backoff on connection loss
 *   - Trigger onSeatUpdate() when "seat_update" messages arrive
 *
 * @param {number|null} eventId  null = do not connect
 * @param {(seat, action) => void} onSeatUpdate
 * @param {(pricing) => void} [onPricingUpdate]  Dynamic pricing updates
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

    // Convert http(s) to ws(s)
    //
    // Token passed via query param because the browser WebSocket API
    // does not support custom headers. Uses short-lived access tokens only.
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

      // Exponential backoff: 1s, 2s, 4s, 8s... max 15s
      //
      // Prevents thundering herd effect on server recovery
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
