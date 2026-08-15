import { useCallback, useEffect, useRef, useState } from 'react'

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
} from './api'
import AuthPage from './auth/AuthPage'
import { useAuth } from './auth/AuthContext'
import BookingPanel from './components/BookingPanel'
import SeatGrid from './components/SeatGrid'
import { useWebSocket } from './hooks/useWebSocket'

export default function App() {
  const { user, loading: authLoading, isAuthenticated } = useAuth()

  // Session restore hone tak kuch mat dikhao — warna ek pal ko login page
  // flash hota hai aur phir gayab ho jata hai.
  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-500">
        Loading…
      </div>
    )
  }

  if (!isAuthenticated) return <AuthPage />

  // key={user.id} — user badalne par poora state fresh. Warna pichhle user
  // ki bookings/selection nayi login me dikh jaati.
  return <BookingApp key={user.id} />
}

function BookingApp() {
  const { user, logout } = useAuth()

  const [health, setHealth] = useState(null)
  const [event, setEvent] = useState(null)
  const [seats, setSeats] = useState([])
  const [bookings, setBookings] = useState([])

  const [selectedSeat, setSelectedSeat] = useState(null)
  const [lockSecondsLeft, setLockSecondsLeft] = useState(0)

  const [locking, setLocking] = useState(false)
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  // Cleanup ke liye latest values chahiye, par unpe effect dobara nahi chalana
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
        const [healthData, events] = await Promise.all([getHealth(), getEvents()])
        setHealth(healthData)

        if (events.length === 0) {
          setFatalError("Koi event nahi mila — 'docker compose exec backend python seed.py' chalao")
          return
        }
        await refresh(events[0].id)
      } catch (err) {
        setFatalError(err.message)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [refresh])

  /**
   * WebSocket se live seat updates — sirf badli hui seat ka update aata hai,
   * chahe change kisi DUSRE user ne kiya ho.
   */
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
   * Lock ka countdown — sirf DIKHANE ke liye.
   * Asli expiry Redis TTL me hoti hai: browser band kar do ya tab crash ho
   * jaye, seat phir bhi 5 min me apne aap free ho jayegi.
   */
  useEffect(() => {
    if (lockSecondsLeft <= 0) return

    const id = setInterval(() => {
      setLockSecondsLeft((s) => {
        if (s <= 1) {
          setSelectedSeat(null)
          setMessage({ type: 'error', text: '⏱️ Hold time khatam, seat wapas available hai' })
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
          keepalive: true,      // page band hote waqt bhi request nikal jati hai
        })
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  async function handleSelect(seat) {
    if (locking) return

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
        await unlockSeat(selectedSeat.id).catch(() => {})
      }

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
      setMessage({ type: 'error', text: err.status === 409 ? `⚠️ ${err.message}` : err.message })
    } finally {
      setLocking(false)
      await refresh(event.id)
    }
  }

  async function releaseCurrentLock() {
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

  async function handleBook() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      await createBooking(selectedSeat.id)
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
      await refresh(event.id)
    }
  }

  async function handleCancel(bookingId) {
    setMessage(null)
    try {
      await cancelBooking(bookingId)
      setMessage({ type: 'success', text: 'Booking cancel ho gayi, seat wapas available hai' })
      await refresh(event.id)
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    }
  }

  if (loading) {
    return (
      <Shell user={user} onLogout={logout}>
        <p className="text-center text-slate-500">Loading…</p>
      </Shell>
    )
  }

  if (fatalError) {
    return (
      <Shell user={user} onLogout={logout}>
        <div className="mx-auto max-w-md rounded-xl border border-rose-900/50 bg-rose-950/30 p-6 text-center">
          <p className="text-rose-300">{fatalError}</p>
          <p className="mt-2 text-xs text-slate-500">
            Backend: <code className="text-slate-400">{API_URL}</code>
          </p>
        </div>
      </Shell>
    )
  }

  // Counts hamesha seats se derive — WebSocket update aate hi apne aap sahi
  const counts = seats.reduce(
    (acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }),
    {},
  )

  return (
    <Shell health={health} user={user} wsStatus={wsStatus} onLogout={logout}>
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <SeatGrid
          seats={seats}
          selectedSeat={selectedSeat}
          onSelect={handleSelect}
          currentUserId={user.id}
          busy={locking}
        />
        <BookingPanel
          event={event}
          counts={counts}
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

function Shell({ children, health, user, wsStatus, onLogout }) {
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

          <div className="flex items-center gap-3 text-xs">
            {health && (
              <span className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 px-3 py-1.5">
                <Dot ok={health.database === 'connected'} />
                <span className="text-slate-400">DB</span>
                <Dot ok={health.redis === 'connected'} />
                <span className="text-slate-400">Redis</span>
                <span
                  className={`h-2 w-2 rounded-full ${
                    wsStatus === 'open'
                      ? 'animate-pulse bg-emerald-400'
                      : wsStatus === 'connecting'
                        ? 'bg-amber-400'
                        : 'bg-rose-500'
                  }`}
                />
                <span className="text-slate-400">
                  {wsStatus === 'open' ? 'Live' : wsStatus === 'connecting' ? 'Connecting' : 'Offline'}
                </span>
              </span>
            )}

            {user && (
              <div className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 py-1 pl-1 pr-3">
                {user.avatar_url ? (
                  <img
                    src={user.avatar_url}
                    alt=""
                    className="h-6 w-6 rounded-full"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-semibold uppercase">
                    {(user.full_name || user.email)[0]}
                  </span>
                )}
                <span className="text-slate-400">{user.full_name || user.email}</span>
                <button
                  onClick={onLogout}
                  className="ml-1 text-slate-600 transition hover:text-rose-400"
                >
                  Logout
                </button>
              </div>
            )}
          </div>
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
