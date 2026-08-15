import { Link, useParams } from 'react-router-dom'

import { useBooking } from '../booking/BookingContext'
import {
  IconCalendar,
  IconPin,
  IconTicket,
  IconUsers,
} from '../layout/icons'

export default function EventDetail() {
  const { id } = useParams()
  const { events, event: loaded, counts } = useBooking()

  // Jo event abhi load hai uske paas description aur price range hai
  // (EventDetail schema). Baaki events sirf list se aate hain.
  const isLoaded = String(loaded?.id) === id
  const event = isLoaded ? loaded : events.find((e) => String(e.id) === id)

  if (!event) {
    return (
      <div className="animate-rise">
        <BackLink />
        <p className="mt-6 text-sm text-slate-500">Event nahi mila.</p>
      </div>
    )
  }

  const starts = new Date(event.starts_at)
  const city = event.venue.split(',').pop().trim()

  return (
    <div className="animate-rise max-w-4xl space-y-5">
      <BackLink />

      <section className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--panel)]">
        <div className="flex flex-col gap-5 p-5 sm:flex-row sm:p-6">
          {/* Poster — gradient + stage beams, koi external image nahi */}
          <div className="relative h-40 shrink-0 overflow-hidden rounded-xl bg-gradient-to-br from-violet-700 via-indigo-900 to-slate-950 sm:h-36 sm:w-52">
            <svg className="absolute inset-0 h-full w-full opacity-70" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <linearGradient id="pbeam" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#c4b5fd" stopOpacity="0.6" />
                  <stop offset="100%" stopColor="#c4b5fd" stopOpacity="0" />
                </linearGradient>
              </defs>
              <polygon points="30,0 70,0 120,160 0,160" fill="url(#pbeam)" />
              <polygon points="140,0 175,0 210,160 110,160" fill="url(#pbeam)" />
            </svg>
            <span className="absolute inset-0 flex items-center justify-center text-4xl">🎤</span>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-white">{event.name}</h1>
              <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-300 ring-1 ring-emerald-500/25">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                Live booking
              </span>
            </div>

            <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-slate-400">
              <span className="flex items-center gap-1.5">
                <IconPin width={14} height={14} className="text-slate-600" />
                {event.venue}
              </span>
              <span className="flex items-center gap-1.5">
                <IconCalendar width={14} height={14} className="text-slate-600" />
                {starts.toLocaleDateString(undefined, {
                  weekday: 'short',
                  day: 'numeric',
                  month: 'short',
                  year: 'numeric',
                })}
                {' · '}
                {starts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>

            <div className="mt-3.5 flex flex-wrap gap-2">
              {event.category && <Tag>🎵 {event.category}</Tag>}
              <Tag>🎬 Live Concert</Tag>
              <Tag>📍 {city}</Tag>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5 sm:p-6">
        <h2 className="text-base font-semibold text-slate-100">About the Event</h2>

        {event.description ? (
          // Paragraphs split — DB me \n\n se alag kiye hain
          <div className="mt-2.5 space-y-3">
            {event.description.split('\n\n').map((para, i) => (
              <p key={i} className="text-sm leading-relaxed text-slate-400">
                {para}
              </p>
            ))}
          </div>
        ) : (
          <p className="mt-2.5 text-sm text-slate-600">Koi description nahi.</p>
        )}

        {/* ⚠️ Ye chips ASLI data se bante hain. Mockup me "50K+ Audience" tha —
            wo fake hota, yahan 100 seats hain. */}
        <div className="mt-5 flex flex-wrap gap-2">
          <Tag>
            <IconTicket width={13} height={13} />
            {event.total_seats} seats
          </Tag>

          {isLoaded && event.min_price != null && (
            <Tag>
              💰 ₹{event.min_price} – ₹{event.max_price}
            </Tag>
          )}

          {isLoaded && (
            <Tag>
              <IconUsers width={13} height={13} />
              {counts.available || 0} available now
            </Tag>
          )}
        </div>
      </section>

      {isLoaded && (
        <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex gap-5">
              <Stat value={counts.available || 0} label="Available" className="text-emerald-400" />
              <Stat value={counts.locked || 0} label="Held" className="text-amber-400" />
              <Stat value={counts.booked || 0} label="Booked" className="text-rose-400" />
            </div>

            <Link
              to="/"
              className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold transition hover:bg-violet-500"
            >
              Select seats
            </Link>
          </div>
        </section>
      )}
    </div>
  )
}

function BackLink() {
  return (
    <Link
      to="/events"
      className="inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-slate-100"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M15 6 9 12l6 6" />
      </svg>
      Back to Events
    </Link>
  )
}

function Tag({ children }) {
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel-2)] px-2.5 py-1.5 text-xs text-slate-300">
      {children}
    </span>
  )
}

function Stat({ value, label, className }) {
  return (
    <div>
      <p className={`text-xl font-bold tabular-nums ${className}`}>{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  )
}
