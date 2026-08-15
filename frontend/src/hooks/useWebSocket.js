import { useCallback, useEffect, useRef, useState } from 'react'

import { API_URL } from '../api'

/**
 * Ek event ke live seat updates ke liye WebSocket.
 *
 * Kaam:
 *   - connect on mount, close on unmount
 *   - connection toote to exponential backoff ke saath reconnect
 *   - "seat_update" message aane par onSeatUpdate() call
 *
 * @param {number|null} eventId  null = abhi connect mat karo
 * @param {(seat, action) => void} onSeatUpdate
 * @returns {{ status: 'connecting'|'open'|'closed' }}
 */
export function useWebSocket(eventId, onSeatUpdate) {
  const [status, setStatus] = useState('connecting')

  const socketRef = useRef(null)
  const retryRef = useRef(0)
  const timerRef = useRef(null)
  // Component unmount ho chuka? Tab reconnect nahi karna.
  const closedByUsRef = useRef(false)

  // Callback ko ref me rakhte hain taki wo badalne par socket dobara
  // na bane. Warna har render pe reconnect hota rehta.
  const handlerRef = useRef(onSeatUpdate)
  handlerRef.current = onSeatUpdate

  const connect = useCallback(() => {
    if (!eventId) return

    // http:// -> ws://  aur  https:// -> wss://
    const wsUrl = `${API_URL.replace(/^http/, 'ws')}/ws/events/${eventId}`
    const socket = new WebSocket(wsUrl)
    socketRef.current = socket
    setStatus('connecting')

    socket.onopen = () => {
      setStatus('open')
      retryRef.current = 0      // successful connect pe backoff reset
    }

    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'seat_update') {
          handlerRef.current?.(msg.seat, msg.action)
        }
      } catch {
        // kachra message — ignore
      }
    }

    socket.onclose = () => {
      setStatus('closed')
      if (closedByUsRef.current) return

      // Exponential backoff: 1s, 2s, 4s, 8s... max 15s
      //
      // Fixed 1s retry kyu nahi: server down ho to 100 clients har second
      // hammer karenge, aur wo uthne hi nahi payega. Backoff usse bachata hai.
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
      retryRef.current += 1
      timerRef.current = setTimeout(connect, delay)
    }

    socket.onerror = () => socket.close()   // close handler retry sambhal lega
  }, [eventId])

  useEffect(() => {
    closedByUsRef.current = false
    connect()

    return () => {
      // Cleanup — warna React StrictMode (dev) me do sockets khul jaate hain
      closedByUsRef.current = true
      clearTimeout(timerRef.current)
      socketRef.current?.close()
    }
  }, [connect])

  return { status }
}
