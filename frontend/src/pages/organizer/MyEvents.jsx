import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { deleteEvent, getMyEvents } from '../../api'
import { useAuth } from '../../auth/AuthContext'
import { IconCalendar, IconPin } from '../../layout/icons'

export default function MyEvents() {
  const { user } = useAuth()
  const location = useLocation()

  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  // CreateEvent page navigate karte waqt state me event ka naam bhejta hai
  const [notice, setNotice] = useState(location.state?.created ?? null)

  const load = useCallback(async () => {
    try {
      setEvents(await getMyEvents())
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleDelete(event) {
    if (!confirm(`"${event.name}" delete karein? Ye wapas nahi aayega.`)) return
    try {
      await deleteEvent(event.id)
      setNotice(null)
      await load()
    } catch (err) {
      // 409 = confirmed bookings hain. Ye normal business rule hai, crash nahi.
      setError(err.message)
    }
  }

  const isAdmin = user.role === 'admin'

  const totals = events.reduce(
    (acc, e) => ({
      seats: acc.seats + e.total_seats,
      booked: acc.booked + e.booked_seats,
      revenue: acc.revenue + e.revenue,
    }),
    { seats: 0, booked: 0, revenue: 0 },
  )

  return (
    <div className="animate-rise space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">
            {isAdmin ? 'All Events' : 'My Events'}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {isAdmin
              ? 'Admin ko platform ke saare events dikhte hain'
              : 'Sirf tumhare banaye hue events'}
          </p>
        </div>

        <Link
          to="/organizer/events/new"
          className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium transition hover:bg-violet-500"
        >
          + Create event
        </Link>
      </header>

      {notice && (
        <p className="rounded-xl bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          ✅ "{notice}" ban gaya
        </p>
      )}
      {error && (
        <p className="rounded-xl bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>
      )}

      {events.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat value={events.length} label="Events" className="text-slate-100" />
          <Stat value={`${totals.booked} / ${totals.seats}`} label="Seats sold" className="text-emerald-400" />
          <Stat value={`₹${totals.revenue.toLocaleString('en-IN')}`} label="Revenue" className="text-violet-300" />
        </div>
      )}

      {loading ? (
        <div className="h-40 animate-pulse rounded-2xl bg-[var(--panel)]" />
      ) : events.length === 0 ? (
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] py-14 text-center">
          <p className="text-3xl">🎪</p>
          <p className="mt-3 text-sm text-slate-400">Abhi koi event nahi</p>
          <Link
            to="/organizer/events/new"
            className="mt-4 inline-block rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium transition hover:bg-violet-500"
          >
            Pehla event banao
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {events.map((event) => {
            const sold = event.total_seats
              ? Math.round((event.booked_seats / event.total_seats) * 100)
              : 0

            return (
              <article
                key={event.id}
                className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5
                           transition hover:border-violet-500/30"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="font-semibold text-slate-100">{event.name}</h2>
                      {event.category && (
                        <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-400">
                          {event.category}
                        </span>
                      )}
                    </div>

                    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                      <span className="flex items-center gap-1.5">
                        <IconPin width={12} height={12} />
                        {event.venue}
                      </span>
                      <span className="flex items-center gap-1.5">
                        <IconCalendar width={12} height={12} />
                        {new Date(event.starts_at).toLocaleString(undefined, {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                  </div>

                  <div className="flex gap-2">
                    <Link
                      to={`/events/${event.id}`}
                      className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs
                                 text-slate-300 transition hover:bg-white/5"
                    >
                      View
                    </Link>
                    <button
                      onClick={() => handleDelete(event)}
                      className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs
                                 text-rose-400 transition hover:bg-rose-500/10"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {/* Sales bar — booked vs held vs available */}
                <div className="mt-4 flex h-2 overflow-hidden rounded-full bg-[var(--panel-2)]">
                  <span
                    className="bg-rose-500/70"
                    style={{ width: `${(event.booked_seats / event.total_seats) * 100}%` }}
                  />
                  <span
                    className="bg-amber-400/70"
                    style={{ width: `${(event.locked_seats / event.total_seats) * 100}%` }}
                  />
                </div>

                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs">
                  <span className="text-emerald-400">{event.available_seats} available</span>
                  <span className="text-amber-400">{event.locked_seats} held</span>
                  <span className="text-rose-400">{event.booked_seats} booked</span>
                  <span className="text-slate-500">{sold}% sold</span>
                  <span className="ml-auto font-semibold text-violet-300">
                    ₹{event.revenue.toLocaleString('en-IN')}
                  </span>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}

function Stat({ value, label, className }) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4">
      <p className={`text-2xl font-bold tabular-nums ${className}`}>{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{label}</p>
    </div>
  )
}
