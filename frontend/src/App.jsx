import { useCallback, useEffect, useState } from 'react'

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
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  /**
   * Event + seats + bookings ek saath refresh karo.
   *
   * Abhi har booking ke baad poora data dubara maang rahe hain.
   * Phase 5 me WebSocket ye replace kar dega — sirf badli hui seat ka
   * update aayega, poori list nahi.
   */
  const refresh = useCallback(
    async (eventId, userId) => {
      const [eventData, seatData, bookingData] = await Promise.all([
        getEvent(eventId),
        getEventSeats(eventId),
        getMyBookings(userId),
      ])
      setEvent(eventData)
      setSeats(seatData)
      setBookings(bookingData)
    },
    [],
  )

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

  async function handleBook() {
    if (!selectedSeat || !user) return

    setBooking(true)
    setMessage(null)
    try {
      await createBooking(selectedSeat.id, user.id)
      setMessage({ type: 'success', text: `Seat ${selectedSeat.row_label}-${selectedSeat.seat_number} book ho gayi!` })
      setSelectedSeat(null)
      await refresh(event.id, user.id)
    } catch (err) {
      // 409 = koi aur pehle le gaya. Ye "error" nahi, expected behaviour hai —
      // isi ko rokne ke liye poora locking system bana hai.
      const text = err.status === 409 ? `⚠️ ${err.message}` : err.message
      setMessage({ type: 'error', text })
      // Seat ki asli haalat dikhane ke liye refresh
      await refresh(event.id, user.id)
    } finally {
      setBooking(false)
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
          onSelect={setSelectedSeat}
        />
        <BookingPanel
          event={event}
          selectedSeat={selectedSeat}
          onBook={handleBook}
          onCancel={handleCancel}
          booking={booking}
          message={message}
          bookings={bookings}
        />
      </div>
    </Shell>
  )
}

/** Page ka frame — header, status badge, footer. */
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
              <span className="flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900 px-3 py-1.5">
                <span
                  className={`h-2 w-2 rounded-full ${
                    health.database === 'connected' ? 'bg-emerald-400' : 'bg-rose-500'
                  }`}
                />
                <span className="text-slate-400">
                  API {health.version} · DB {health.database}
                </span>
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

export default App
