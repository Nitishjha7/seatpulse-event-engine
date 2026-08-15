import { Link } from 'react-router-dom'

import { bookingRef } from './BookingConfirmedModal'

/**
 * Bookings ki list. Dashboard ke right rail me (compact) aur
 * My Bookings page pe (full) — dono jagah yahi component.
 */
export default function BookingsList({ bookings, onCancel, compact = false, limit }) {
  const confirmed = bookings.filter((b) => b.status === 'confirmed')
  const shown = limit ? bookings.slice(0, limit) : bookings

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-300">
          My Bookings{' '}
          <span className="text-slate-600">({confirmed.length})</span>
        </h2>

        {compact && bookings.length > (limit ?? 0) && (
          <Link to="/bookings" className="text-xs text-violet-400 hover:text-violet-300">
            View all
          </Link>
        )}
      </div>

      {bookings.length === 0 ? (
        <div className="py-7 text-center">
          <p className="text-sm text-slate-500">Abhi koi booking nahi</p>
          {compact && (
            <Link
              to="/events"
              className="mt-3 inline-block rounded-lg border border-[var(--border)] px-3 py-1.5
                         text-xs text-slate-300 transition hover:bg-white/5"
            >
              Browse events
            </Link>
          )}
        </div>
      ) : (
        <ul className="mt-3 space-y-2">
          {shown.map((b) => (
            <li
              key={b.id}
              className="flex items-center gap-3 rounded-xl bg-[var(--panel-2)] px-3 py-2.5
                         transition hover:bg-white/[0.06]"
            >
              <span
                className={`flex h-9 w-11 shrink-0 items-center justify-center rounded-lg
                            text-xs font-bold ${
                              b.status === 'confirmed'
                                ? 'bg-violet-500/15 text-violet-300'
                                : 'bg-white/5 text-slate-600'
                            }`}
              >
                {b.seat_label}
              </span>

              <div className="min-w-0 flex-1">
                <p
                  className={`truncate text-sm ${
                    b.status === 'cancelled'
                      ? 'text-slate-600 line-through'
                      : 'text-slate-200'
                  }`}
                >
                  {b.event_name}
                </p>
                <p className="truncate text-xs text-slate-500">
                  ₹{b.amount} ·{' '}
                  {new Date(b.created_at).toLocaleDateString(undefined, {
                    day: 'numeric',
                    month: 'short',
                  })}
                  {!compact && (
                    <span className="ml-1.5 font-mono text-slate-600">
                      {bookingRef(b.id)}
                    </span>
                  )}
                </p>
              </div>

              {b.status === 'confirmed' ? (
                <button
                  onClick={() => onCancel(b.id)}
                  className="shrink-0 rounded-lg px-2 py-1 text-xs text-rose-400
                             transition hover:bg-rose-500/10 hover:text-rose-300"
                >
                  Cancel
                </button>
              ) : (
                <span className="shrink-0 text-xs text-slate-600">cancelled</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
