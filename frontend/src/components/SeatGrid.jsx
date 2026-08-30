import { seatPrice } from '../booking/BookingContext'

/**
 * Seat grid — event ki saari seats rows me.
 *
 * WebSocket update aane par sirf badli hui seat re-render hoti hai
 * (state me pura array replace nahi hota, sirf ek item).
 */

// Har status ka look ek jagah — grid aur legend kabhi alag na dikhein
const SEAT_STYLES = {
  available:
    'bg-emerald-500/85 text-emerald-950 hover:bg-emerald-400 hover:-translate-y-0.5 cursor-pointer',
  locked: 'bg-amber-400/80 text-amber-950 cursor-not-allowed',
  // Payment chal raha hai — locked se alag rang, taki dusre users ko dikhe
  // ki ye seat bikne ke kagaar pe hai, sirf hold me nahi
  payment_pending: 'bg-orange-600/80 text-orange-50 cursor-not-allowed animate-pulse',
  booked: 'bg-rose-600/70 text-rose-100/70 cursor-not-allowed line-through',
  // Meri hold — dusre ki hold (peeli) se साफ alag dikhni chahiye
  selected:
    'bg-violet-500 text-white ring-2 ring-violet-300 ring-offset-2 ring-offset-[var(--panel)] cursor-pointer',
}

function Seat({ seat, isSelected, isMine, onSelect, busy }) {
  // Available click kar sakte ho, aur apni hold bhi (deselect ke liye)
  const clickable = !busy && (seat.status === 'available' || isMine)
  const style = isSelected
    ? SEAT_STYLES.selected
    : SEAT_STYLES[seat.status] || SEAT_STYLES.available

  return (
    <button
      onClick={() => clickable && onSelect(seat)}
      disabled={!clickable}
      title={`${seat.row_label}-${seat.seat_number} · ₹${seatPrice(seat)} · ${seat.status}`}
      className={`h-8 w-8 shrink-0 rounded-md text-[11px] font-semibold transition-all
                  duration-150 sm:h-9 sm:w-9 sm:text-xs ${style} ${busy ? 'opacity-60' : ''}`}
    >
      {seat.seat_number}
    </button>
  )
}

export default function SeatGrid({ seats, selectedSeat, onSelect, currentUserId, busy }) {
  // Flat list ko rows me todo. Backend already sorted bhejta hai.
  const rows = seats.reduce((acc, seat) => {
    ;(acc[seat.row_label] ||= []).push(seat)
    return acc
  }, {})

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4 sm:p-5">
      {/* Stage — bina iske samajh nahi aata ki aage kaunsi taraf hai */}
      <div className="relative mx-auto mb-7 w-4/5 max-w-md">
        <div
          className="rounded-b-3xl border-b-2 border-violet-500/40 bg-gradient-to-b
                     from-violet-500/20 to-transparent py-2 text-center text-[11px]
                     font-semibold uppercase tracking-[0.25em] text-violet-300/80"
        >
          Stage
        </div>
      </div>

      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <div className="inline-block min-w-full space-y-2">
          {Object.entries(rows).map(([rowLabel, rowSeats]) => (
            <div key={rowLabel} className="flex items-center gap-1.5 sm:gap-2">
              <span className="w-4 shrink-0 text-xs font-semibold text-slate-500">
                {rowLabel}
              </span>
              {rowSeats.map((seat) => (
                <Seat
                  key={seat.id}
                  seat={seat}
                  isSelected={selectedSeat?.id === seat.id}
                  isMine={seat.status === 'locked' && seat.locked_by === currentUserId}
                  onSelect={onSelect}
                  busy={busy}
                />
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 border-t border-[var(--border)] pt-4 text-xs text-slate-400">
        <Legend className="bg-emerald-500/85" label="Available" />
        <Legend className="bg-violet-500" label="Your hold" />
        <Legend className="bg-amber-400/80" label="Held by someone else" />
        <Legend className="bg-orange-600/80" label="Being purchased" />
        <Legend className="bg-rose-600/70" label="Booked" />
      </div>
    </section>
  )
}

function Legend({ className, label }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-3 w-3 rounded ${className}`} />
      {label}
    </span>
  )
}
