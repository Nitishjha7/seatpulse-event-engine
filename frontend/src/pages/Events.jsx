import { Link } from 'react-router-dom'

import { useBooking } from '../booking/BookingContext'
import { IconCalendar, IconPin, IconTicket } from '../layout/icons'

export default function Events() {
  const { events, event: activeEvent, counts } = useBooking()

  return (
    <div className="animate-rise space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Events</h1>
        <p className="mt-1 text-sm text-slate-500">
          {events.length} event{events.length === 1 ? '' : 's'} available
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {events.map((ev) => {
          const isActive = ev.id === activeEvent?.id
          const starts = new Date(ev.starts_at)

          return (
            <article
              key={ev.id}
              className="overflow-hidden rounded-2xl border border-[var(--border)]
                         bg-[var(--panel)] transition hover:border-violet-500/30"
            >
              {/* Chhota gradient banner — hero jaisa, par compact */}
              <div className="relative h-24 bg-gradient-to-br from-violet-800 via-indigo-900 to-slate-950">
                <span
                  className="absolute left-3 top-3 rounded-full bg-black/40 px-2 py-0.5
                             text-[10px] uppercase tracking-wide text-violet-200 backdrop-blur"
                >
                  Live concert
                </span>
              </div>

              <div className="p-4">
                <h2 className="font-semibold text-slate-100">{ev.name}</h2>

                <div className="mt-2 space-y-1 text-xs text-slate-400">
                  <p className="flex items-center gap-1.5">
                    <IconPin width={13} height={13} className="text-slate-600" />
                    {ev.venue}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <IconCalendar width={13} height={13} className="text-slate-600" />
                    {starts.toLocaleDateString(undefined, {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <IconTicket width={13} height={13} className="text-slate-600" />
                    {ev.total_seats} seats
                  </p>
                </div>

                {/* Live counts sirf us event ke hain jo abhi load hai.
                    Multi-event support aane par har card apna data layega. */}
                {isActive && (
                  <div className="mt-3 flex gap-3 border-t border-[var(--border)] pt-3 text-xs">
                    <span className="text-emerald-400">{counts.available || 0} available</span>
                    <span className="text-amber-400">{counts.locked || 0} held</span>
                    <span className="text-rose-400">{counts.booked || 0} booked</span>
                  </div>
                )}

                <div className="mt-4 flex gap-2">
                  <Link
                    to={`/events/${ev.id}`}
                    className="flex-1 rounded-xl border border-[var(--border)] py-2 text-center
                               text-sm text-slate-300 transition hover:bg-white/5"
                  >
                    Details
                  </Link>
                  {isActive && (
                    <Link
                      to="/"
                      className="flex-1 rounded-xl bg-violet-600 py-2 text-center text-sm
                                 font-medium transition hover:bg-violet-500"
                    >
                      Select seats
                    </Link>
                  )}
                </div>
              </div>
            </article>
          )
        })}
      </div>

      <p className="text-xs text-slate-600">
        Abhi ek hi event seed hota hai. Organizer portal aane par yahan se
        events create honge — roadmap me hai.
      </p>
    </div>
  )
}
