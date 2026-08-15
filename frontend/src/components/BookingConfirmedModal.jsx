import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { IconClose, IconPin } from '../layout/icons'
import Confetti from './Confetti'

/** Booking id ko reference number jaisa dikhao: 42 -> SP00042 */
export function bookingRef(id) {
  return `SP${String(id).padStart(5, '0')}`
}

/**
 * Booking confirm hone par success modal.
 *
 * Saara data ASLI hai — booking id database se, seat aur price us seat se
 * jo user ne hold ki thi. Koi placeholder nahi.
 */
export default function BookingConfirmedModal({ booking, seat, event, onClose }) {
  // Escape se band ho — har modal me ye hona chahiye
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)

    // Modal khula ho to background scroll na ho
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  if (!booking) return null

  return (
    <div
      className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="booking-confirmed-title"
    >
      <div
        // Andar click karne pe band na ho
        onClick={(e) => e.stopPropagation()}
        className="animate-pop-in relative w-full max-w-sm overflow-hidden rounded-2xl
                   border border-violet-500/25 bg-[var(--panel)] p-6 text-center
                   shadow-2xl shadow-violet-950/40"
      >
        <Confetti />

        <button
          onClick={onClose}
          className="absolute right-3 top-3 text-slate-500 transition hover:text-slate-200"
          aria-label="Close"
        >
          <IconClose />
        </button>

        {/* Success tick */}
        <div className="relative mx-auto flex h-14 w-14 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-emerald-500/20 blur-md" />
          <span className="relative flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="m5 13 4 4L19 7" />
            </svg>
          </span>
        </div>

        <h2 id="booking-confirmed-title" className="relative mt-4 text-xl font-bold text-white">
          Booking Confirmed!
        </h2>
        <p className="relative mt-1 text-sm text-slate-400">
          Your seat has been successfully booked.
        </p>

        {/* Event */}
        <div className="relative mt-5 flex items-center gap-3 rounded-xl bg-[var(--panel-2)] p-3 text-left">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 text-lg">
            🎤
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-100">{event?.name}</p>
            <p className="flex items-center gap-1 truncate text-xs text-slate-500">
              <IconPin width={11} height={11} />
              {event?.venue}
            </p>
          </div>
        </div>

        {/* Details */}
        <div className="relative mt-3 grid grid-cols-3 gap-2">
          <Detail label="Seat No." value={seat ? `${seat.row_label}-${seat.seat_number}` : '—'} />
          <Detail label="Price" value={`₹${booking.amount}`} />
          <Detail label="Booking ID" value={bookingRef(booking.id)} mono />
        </div>

        <Link
          to="/bookings"
          onClick={onClose}
          className="relative mt-5 block rounded-xl bg-violet-600 py-2.5 text-sm font-semibold
                     transition hover:bg-violet-500"
        >
          View My Bookings
        </Link>

        <button
          onClick={onClose}
          className="relative mt-2 w-full py-1.5 text-sm text-slate-500 transition hover:text-slate-300"
        >
          Back to seat map
        </button>
      </div>
    </div>
  )
}

function Detail({ label, value, mono }) {
  return (
    <div className="rounded-lg bg-[var(--panel-2)] px-2 py-2">
      <p className="text-[9px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-0.5 truncate text-sm font-semibold text-slate-100 ${mono ? 'font-mono text-xs' : ''}`}>
        {value}
      </p>
    </div>
  )
}
