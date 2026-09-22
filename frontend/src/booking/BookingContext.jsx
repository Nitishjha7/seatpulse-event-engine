import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

import {
  API_URL,
  cancelBooking,
  createBooking,
  createGroup,
  getAccessToken,
  startCheckout,
  getEvent,
  getEventSeats,
  getEvents,
  getHealth,
  getMyBookings,
  lockSeat,
  unlockSeat,
} from '../api'
import { useAuth } from '../auth/AuthContext'
import { useWebSocket } from '../hooks/useWebSocket'

const BookingContext = createContext(null)

/**
 * Effective price for a seat: held_price (locked at hold time) > current_price
 * (live dynamic price) > price (base). Centralized so no component accidentally
 * skips the held price.
 */
export function seatPrice(seat) {
  if (!seat) return null
  return seat.held_price ?? seat.current_price ?? seat.price
}

/** Converts error objects into user-friendly messages. */
function errorText(err) {
  if (err.status === 429) {
    const wait = err.retryAfter ? ` Please wait ${err.retryAfter} seconds and try again.` : ''
    return `🐢 Slow down!${wait}`
  }
  if (err.status === 409) return `${err.message}`
  return err.message
}

export function useBooking() {
  const ctx = useContext(BookingContext)
  if (!ctx) throw new Error('useBooking must be used within a BookingProvider')
  return ctx
}

/**
 * Global booking state, shared across Dashboard/My Bookings/Events so they
 * all read from one WebSocket connection instead of each opening their own.
 */
export function BookingProvider({ children }) {
  const { user } = useAuth()

  const [health, setHealth] = useState(null)
  const [events, setEvents] = useState([])
  const [event, setEvent] = useState(null)
  const [seats, setSeats] = useState([])
  const [bookings, setBookings] = useState([])
  // Event demand/surge state. Kept separate from the event object to avoid
  // re-rendering the entire event object on every pricing update.
  const [pricing, setPricing] = useState(null)

  const [selectedSeat, setSelectedSeat] = useState(null)
  const [lockSecondsLeft, setLockSecondsLeft] = useState(0)
  // Data for the success modal after a booking is confirmed: { booking, seat, event }
  const [lastBooking, setLastBooking] = useState(null)

  const [locking, setLocking] = useState(false)
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  const selectedRef = useRef(null)
  selectedRef.current = selectedSeat

  const eventIdRef = useRef(null)
  const pricingRefetchRef = useRef(null)

  const refresh = useCallback(async (eventId) => {
    const [eventData, seatData, bookingData] = await Promise.all([
      getEvent(eventId),
      getEventSeats(eventId),
      getMyBookings(),
    ])
    setEvent(eventData)
    setSeats(seatData)
    setBookings(bookingData)
    setPricing(eventData.pricing ?? null)
    eventIdRef.current = eventId
  }, [])

  useEffect(() => {
    async function init() {
      try {
        const [healthData, eventList] = await Promise.all([getHealth(), getEvents()])
        setHealth(healthData)
        setEvents(eventList)

        if (eventList.length === 0) {
          setFatalError(
            "No events found — run 'docker compose exec backend python seed.py'",
          )
          return
        }
        await refresh(eventList[0].id)
      } catch (err) {
        setFatalError(err.message)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [refresh])

  /** Updates seat status via WebSocket. */
  const handleSeatUpdate = useCallback(
    (updatedSeat) => {
      setSeats((prev) => prev.map((s) => (s.id === updatedSeat.id ? updatedSeat : s)))

      // Clear selection if the seat is no longer held by the current user
      setSelectedSeat((prev) => {
        if (!prev || prev.id !== updatedSeat.id) return prev
        const stillMine =
          updatedSeat.status === 'locked' && updatedSeat.locked_by === user.id
        if (stillMine) return updatedSeat
        setLockSecondsLeft(0)
        return null
      })
    },
    [user.id],
  )

  /**
   * We don't compute base × multiplier client-side — JS/Python float rounding
   * can disagree, so we always trust the server's number here.
   */
  const handlePricingUpdate = useCallback((next) => {
    setPricing(next)

    // Debounce refetching to prevent excessive API calls during high traffic
    clearTimeout(pricingRefetchRef.current)
    pricingRefetchRef.current = setTimeout(() => {
      const id = eventIdRef.current
      if (id) getEventSeats(id).then(setSeats).catch(() => {})
    }, 400)
  }, [])

  const { status: wsStatus } = useWebSocket(
    event?.id ?? null,
    handleSeatUpdate,
    handlePricingUpdate,
  )

  useEffect(() => () => clearTimeout(pricingRefetchRef.current), [])

  /** Countdown display only — actual expiry is a Redis TTL on the server. */
  useEffect(() => {
    if (lockSecondsLeft <= 0) return

    const id = setInterval(() => {
      setLockSecondsLeft((s) => {
        if (s <= 1) {
          setSelectedSeat(null)
          setMessage({
            type: 'error',
            text: '⏱️ Hold time expired — seat is now available',
          })
          if (event) refresh(event.id)
          return 0
        }
        return s - 1
      })
    }, 1000)

    return () => clearInterval(id)
  }, [lockSecondsLeft, event, refresh])

  // Release lock on tab close to avoid waiting for TTL
  useEffect(() => {
    const handler = () => {
      const seat = selectedRef.current
      const token = getAccessToken()
      if (seat && token) {
        fetch(`${API_URL}/api/seats/${seat.id}/lock`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
          keepalive: true,
        })
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  async function selectSeat(seat) {
    if (locking) return

    if (selectedSeat?.id === seat.id) {
      await releaseHold()
      return
    }

    setLocking(true)
    setMessage(null)
    try {
      if (selectedSeat) await unlockSeat(selectedSeat.id).catch(() => {})

      const lock = await lockSeat(seat.id)
      setSelectedSeat({ ...seat, held_price: lock.price })
      setLockSecondsLeft(lock.expires_in)
      setMessage({
        type: 'success',
        text: `Seat ${seat.row_label}-${seat.seat_number} held`,
      })
    } catch (err) {
      setSelectedSeat(null)
      setLockSecondsLeft(0)
      setMessage({ type: 'error', text: errorText(err) })
    } finally {
      setLocking(false)
      await refresh(event.id)
    }
  }

  async function releaseHold() {
    if (!selectedSeat) return
    try {
      await unlockSeat(selectedSeat.id)
    } catch {
      /* Ignore if already expired */
    }
    setSelectedSeat(null)
    setLockSecondsLeft(0)
    setMessage(null)
    await refresh(event.id)
  }

  /** Booking is only finalized via webhook after payment confirms, not here. */
  async function payForSeat() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const session = await startCheckout(selectedSeat.id)
      window.location.href = session.checkout_url
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
      setBooking(false)
      await refresh(event.id)
    }
  }

  /** Picks adjacent seats as a suggestion — server validation is the real source of truth. */
  async function startGroup(size) {
    if (!selectedSeat) return

    const sameRow = seats
      .filter((s) => s.row_label === selectedSeat.row_label)
      .sort((a, b) => a.seat_number - b.seat_number)

    const start = sameRow.findIndex((s) => s.id === selectedSeat.id)
    const picked = [selectedSeat.id]

    for (let i = start + 1; i < sameRow.length && picked.length < size; i++) {
      if (sameRow[i].status === 'available') picked.push(sameRow[i].id)
    }
    for (let i = start - 1; i >= 0 && picked.length < size; i--) {
      if (sameRow[i].status === 'available') picked.push(sameRow[i].id)
    }

    if (picked.length < size) {
      setMessage({
        type: 'error',
        text: `Could not find ${size} adjacent seats in this row — try a different row`,
      })
      return
    }

    setBooking(true)
    setMessage(null)
    try {
      const group = await createGroup(picked, 30)
      setSelectedSeat(null)
      setLockSecondsLeft(0)
      return group.share_token
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
    } finally {
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function confirmBooking() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const created = await createBooking(selectedSeat.id)
      setLastBooking({ booking: created, seat: selectedSeat, event })
      setSelectedSeat(null)
      setLockSecondsLeft(0)
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
    } finally {
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function cancel(bookingId) {
    setMessage(null)
    try {
      await cancelBooking(bookingId)
      setMessage({ type: 'success', text: 'Booking cancelled — seat is now available' })
      await refresh(event.id)
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    }
  }

  const counts = seats.reduce(
    (acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }),
    {},
  )

  const value = {
    health,
    wsStatus,
    events,
    event,
    seats,
    counts,
    pricing,
    seatPrice,
    bookings,
    selectedSeat,
    lockSecondsLeft,
    lastBooking,
    locking,
    booking,
    message,
    loading,
    fatalError,
    selectSeat,
    releaseHold,
    confirmBooking,
    payForSeat,
    startGroup,
    cancel,
    dismissLastBooking: () => setLastBooking(null),
    clearMessage: () => setMessage(null),
  }

  return <BookingContext.Provider value={value}>{children}</BookingContext.Provider>
}
