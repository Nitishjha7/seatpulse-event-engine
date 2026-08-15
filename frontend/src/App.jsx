import { useCallback, useEffect, useRef, useState } from 'react'

import {
  API_URL,
  cancelBooking,
  createBooking,
  getEvent,
  getEventSeats,
  getEvents,
  getHealth,
  getMe,
  getMyBookings,
  lockSeat,
  unlockSeat,
} from './api'
import BookingPanel from './components/BookingPanel'
import SeatGrid from './components/SeatGrid'

function App() {
  const [health, setHealth] = useState(null)
  const [user, setUser] = useState(null)

  const [event, setEvent] = useState(null)
  const [seats, setSeats] = useState([])
  const [bookings, setBookings] = useState([])

  const [selectedSeat, setSelectedSeat] = useState(null)
  // Lock kitne second aur chalega. Countdown isi se chalta hai.
  const [lockSecondsLeft, setLockSecondsLeft] = useState(0)

  const [locking, setLocking] = useState(false)
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  // Cleanup ke liye latest values chahiye, par unpe effect dobara nahi chalana.
  // Isliye ref me rakhte hain.
  const selectedRef = useRef(null)
  const userRef = useRef(null)
  selectedRef.current = selectedSeat
  userRef.current = user

  const refresh = useCallback(async (eventId, userId) => {
    const [eventData, seatData, bookingData] = await Promise.all([
      getEvent(eventId),
      getEventSeats(eventId),
      getMyBookings(userId),
    ])
    setEvent(eventData)
    setSeats(seatData)
    setBookings(bookingData)
  }, [])

  // Pehli baar sab load karo
  useEffect(() => {
    async function init() {
      try {
        const [healthData, me, events] = await Promise.all([
          getHealth(),
          getMe(),
          getEvents(),
        ])
        setHealth(healthData)
        setUser(me)

        if (events.length === 0) {
          setFatalError("Koi event nahi mila — 'docker compose exec backend python seed.py' chalao")
          return
        }
        await refresh(events[0].id, me.id)
      } catch (err) {
        setFatalError(err.message)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [refresh])

  /**
   * Lock ka countdown.
   *
   * Ye sirf DIKHANE ke liye hai. Asli expiry Redis me hoti hai (TTL) —
   * browser band kar do ya tab crash ho jaye, seat phir bhi 5 min me
   * apne aap free ho jayegi. Ye timer sirf user ko batata hai kitna time hai.
   */
  useEffect(() => {
    if (lockSecondsLeft <= 0) return

    const id = setInterval(() => {
      setLockSecondsLeft((s) => {
        if (s <= 1) {
          // Time khatam — selection hata do aur seats refresh karo
          setSelectedSeat(null)
          setMessage({ type: 'error', text: '⏱️ Hold time khatam, seat wapas available hai' })
          if (event && user) refresh(event.id, user.id)
          return 0
        }
        return s - 1
      })
    }, 1000)

    return () => clearInterval(id)
  }, [lockSecondsLeft, event, user, refresh])

  // Tab band karte waqt lock chhod do — TTL ka wait na karna pade
  useEffect(() => {
    const handler = () => {
      const seat = selectedRef.current
      const u = userRef.current
      if (seat && u) {
        // keepalive: page band hote waqt bhi request nikal jati hai
        fetch(`${API_URL}/api/seats/${seat.id}/lock?user_id=${u.id}`, {
          method: 'DELETE',
          keepalive: true,
        })
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  /**
   * Seat pe click.
   *
   * Phase 3 me ye sirf local state set karta tha. Ab ye server se
   * actually lock maangta hai — 409 mile to matlab koi aur pehle le gaya.
   */
  async function handleSelect(seat) {
    if (!user || locking) return

    // Wahi seat dubara click = deselect + lock release
    if (selectedSeat?.id === seat.id) {
      await releaseCurrentLock()
      return
    }

    setLocking(true)
    setMessage(null)
    try {
      // Purani seat ka lock pehle chhodo, warna do seats hold rahengi
      if (selectedSeat) {
        await unlockSeat(selectedSeat.id, user.id).catch(() => {})
      }

      const lock = await lockSeat(seat.id, user.id)
      setSelectedSeat(seat)
      setLockSecondsLeft(lock.expires_in)
      setMessage({
        type: 'success',
        text: `Seat ${seat.row_label}-${seat.seat_number} hold ho gayi`,
      })
    } catch (err) {
      setSelectedSeat(null)
      setLockSecondsLeft(0)
      setMessage({ type: 'error', text: err.status === 409 ? `⚠️ ${err.message}` : err.message })
    } finally {
      setLocking(false)
      await refresh(event.id, user.id)
    }
  }

  async function releaseCurrentLock() {
    if (!selectedSeat || !user) return
    try {
      await unlockSeat(selectedSeat.id, user.id)
    } catch {
      // lock TTL pe expire ho chuka hoga — koi baat nahi
    }
    setSelectedSeat(null)
    setLockSecondsLeft(0)
    setMessage(null)
    await refresh(event.id, user.id)
  }

  async function handleBook() {
    if (!selectedSeat || !user) return

    setBooking(true)
    setMessage(null)
    try {
      await createBooking(selectedSeat.id, user.id)
      setMessage({
        type: 'success',
        text: `Seat ${selectedSeat.row_label}-${selectedSeat.seat_number} book ho gayi!`,
      })
      setSelectedSeat(null)
      setLockSecondsLeft(0)
    } catch (err) {
      setMessage({ type: 'error', text: err.status === 409 ? `⚠️ ${err.message}` : err.message })
    } finally {
      setBooking(false)
      await refresh(event.id, user.id)
    }
  }

  async function handleCancel(bookingId) {
    setMessage(null)
    try {
      await cancelBooking(bookingId)
      setMessage({ type: 'success', text: 'Booking cancel ho gayi, seat wapas available hai' })
      await refresh(event.id, user.id)
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    }
  }

  if (loading) {
    return (
      <Shell>
        <p className="text-center text-slate-500">Loading…</p>
      </Shell>
    )
  }

  if (fatalError) {
    return (
      <Shell>
        <div className="mx-auto max-w-md rounded-xl border border-rose-900/50 bg-rose-950/30 p-6 text-center">
          <p className="text-rose-300">{fatalError}</p>
          <p className="mt-2 text-xs text-slate-500">
            Backend: <code className="text-slate-400">{API_URL}</code>
          </p>
        </div>
      </Shell>
    )
  }

  return (
    <Shell health={health} user={user}>
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <SeatGrid
          seats={seats}
          selectedSeat={selectedSeat}
          onSelect={handleSelect}
          currentUserId={user?.id}
          busy={locking}
        />
        <BookingPanel
          event={event}
          selectedSeat={selectedSeat}
          lockSecondsLeft={lockSecondsLeft}
          onBook={handleBook}
          onRelease={releaseCurrentLock}
          onCancel={handleCancel}
          booking={booking}
          message={message}
          bookings={bookings}
        />
      </div>
    </Shell>
  )
}

function Shell({ children, health, user }) {
  return (
    <div className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">🎟️ SeatPulse</h1>
            <p className="text-sm text-slate-500">
              High-Concurrency Event Booking Engine
            </p>
          </div>

          {health && (
            <div className="flex items-center gap-3 text-xs">
              {user && <span className="text-slate-500">{user.email}</span>}
              <span className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 px-3 py-1.5">
                <Dot ok={health.database === 'connected'} />
                <span className="text-slate-400">DB</span>
                <Dot ok={health.redis === 'connected'} />
                <span className="text-slate-400">Redis</span>
                <span className="text-slate-600">· v{health.version}</span>
              </span>
            </div>
          )}
        </header>

        {children}

        <footer className="mt-8 text-center text-xs text-slate-600">
          <a
            href={`${API_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="text-indigo-400 hover:text-indigo-300"
          >
            API docs
          </a>
        </footer>
      </div>
    </div>
  )
}

function Dot({ ok }) {
  return <span className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-rose-500'}`} />
}

export default App
