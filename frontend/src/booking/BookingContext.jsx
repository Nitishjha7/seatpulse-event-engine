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
  getAccessToken,
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

export function useBooking() {
  const ctx = useContext(BookingContext)
  if (!ctx) throw new Error('useBooking ko BookingProvider ke andar hi use karo')
  return ctx
}

/**
 * Poora booking state ek jagah.
 *
 * Pehle ye sab App.jsx me tha. Ab multiple pages (Dashboard, My Bookings,
 * Events) ko same data chahiye — aur sabse important, **WebSocket connection
 * ek hi rehna chahiye**. Har page apna socket kholta to server pe 4 gunа
 * connections ban jaate aur updates duplicate aate.
 */
export function BookingProvider({ children }) {
  const { user } = useAuth()

  const [health, setHealth] = useState(null)
  const [events, setEvents] = useState([])
  const [event, setEvent] = useState(null)
  const [seats, setSeats] = useState([])
  const [bookings, setBookings] = useState([])

  const [selectedSeat, setSelectedSeat] = useState(null)
  const [lockSecondsLeft, setLockSecondsLeft] = useState(0)
  // Booking confirm hone ke baad success modal ka data: { booking, seat, event }
  const [lastBooking, setLastBooking] = useState(null)

  const [locking, setLocking] = useState(false)
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  // Cleanup me latest value chahiye, par uspe effect dobara nahi chalana
  const selectedRef = useRef(null)
  selectedRef.current = selectedSeat

  const refresh = useCallback(async (eventId) => {
    const [eventData, seatData, bookingData] = await Promise.all([
      getEvent(eventId),
      getEventSeats(eventId),
      getMyBookings(),
    ])
    setEvent(eventData)
    setSeats(seatData)
    setBookings(bookingData)
  }, [])

  useEffect(() => {
    async function init() {
      try {
        const [healthData, eventList] = await Promise.all([getHealth(), getEvents()])
        setHealth(healthData)
        setEvents(eventList)

        if (eventList.length === 0) {
          setFatalError(
            "Koi event nahi mila — 'docker compose exec backend python seed.py' chalao",
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

  /** WebSocket se live seat updates — sirf badli hui seat replace hoti hai. */
  const handleSeatUpdate = useCallback(
    (updatedSeat) => {
      setSeats((prev) => prev.map((s) => (s.id === updatedSeat.id ? updatedSeat : s)))

      // Meri hold kisi aur ke paas chali gayi? Selection saaf karo
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

  const { status: wsStatus } = useWebSocket(event?.id ?? null, handleSeatUpdate)

  /**
   * Hold ka countdown — sirf DIKHANE ke liye.
   * Asli expiry Redis TTL me hoti hai: browser band kar do ya tab crash ho
   * jaye, seat phir bhi 5 min me apne aap free ho jayegi.
   */
  useEffect(() => {
    if (lockSecondsLeft <= 0) return

    const id = setInterval(() => {
      setLockSecondsLeft((s) => {
        if (s <= 1) {
          setSelectedSeat(null)
          setMessage({
            type: 'error',
            text: '⏱️ Hold time khatam — seat wapas available hai',
          })
          if (event) refresh(event.id)
          return 0
        }
        return s - 1
      })
    }, 1000)

    return () => clearInterval(id)
  }, [lockSecondsLeft, event, refresh])

  // Tab band karte waqt lock chhod do — TTL ka wait na karna pade
  useEffect(() => {
    const handler = () => {
      const seat = selectedRef.current
      const token = getAccessToken()
      if (seat && token) {
        fetch(`${API_URL}/api/seats/${seat.id}/lock`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
          keepalive: true, // page band hote waqt bhi request nikal jati hai
        })
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  async function selectSeat(seat) {
    if (locking) return

    // Wahi seat dubara click = deselect + lock release
    if (selectedSeat?.id === seat.id) {
      await releaseHold()
      return
    }

    setLocking(true)
    setMessage(null)
    try {
      // Purani seat ka lock pehle chhodo, warna do seats hold rahengi
      if (selectedSeat) await unlockSeat(selectedSeat.id).catch(() => {})

      const lock = await lockSeat(seat.id)
      setSelectedSeat(seat)
      setLockSecondsLeft(lock.expires_in)
      setMessage({
        type: 'success',
        text: `Seat ${seat.row_label}-${seat.seat_number} hold ho gayi`,
      })
    } catch (err) {
      setSelectedSeat(null)
      setLockSecondsLeft(0)
      setMessage({
        type: 'error',
        text: err.status === 409 ? `⚠️ ${err.message}` : err.message,
      })
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
      /* lock TTL pe expire ho chuka hoga — koi baat nahi */
    }
    setSelectedSeat(null)
    setLockSecondsLeft(0)
    setMessage(null)
    await refresh(event.id)
  }

  async function confirmBooking() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const created = await createBooking(selectedSeat.id)

      // Success modal ke liye. Seat aur event abhi capture kar rahe hain,
      // kyunki neeche selectedSeat null ho jayega aur refresh ke baad wo
      // seat 'booked' ho chuki hogi.
      setLastBooking({ booking: created, seat: selectedSeat, event })

      setSelectedSeat(null)
      setLockSecondsLeft(0)
    } catch (err) {
      setMessage({
        type: 'error',
        text: err.status === 409 ? `⚠️ ${err.message}` : err.message,
      })
    } finally {
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function cancel(bookingId) {
    setMessage(null)
    try {
      await cancelBooking(bookingId)
      setMessage({ type: 'success', text: 'Booking cancel — seat wapas available hai' })
      await refresh(event.id)
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    }
  }

  // Counts hamesha seats se derive — WebSocket update aate hi apne aap sahi.
  // Server se dobara poochne ki zaroorat nahi.
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
    cancel,
    dismissLastBooking: () => setLastBooking(null),
    clearMessage: () => setMessage(null),
  }

  return <BookingContext.Provider value={value}>{children}</BookingContext.Provider>
}
