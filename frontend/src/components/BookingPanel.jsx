/**
 * Right side ka panel — event summary, hold ki hui seat + countdown,
 * book button, aur meri bookings.
 */

/** 125 -> "2:05" */
function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function BookingPanel({
  event,
  counts,
  selectedSeat,
  lockSecondsLeft,
  onBook,
  onRelease,
  onCancel,
  booking,
  message,
  bookings,
}) {
  // Aakhri 60 second me countdown laal ho jata hai — user ko jaldi karni chahiye
  const urgent = lockSecondsLeft > 0 && lockSecondsLeft <= 60

  return (
    <div className="space-y-4">
      {/* Event summary */}
      {event && (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="font-semibold text-slate-100">{event.name}</h2>
          <p className="mt-0.5 text-sm text-slate-400">{event.venue}</p>
          <p className="mt-0.5 text-xs text-slate-500">
            {new Date(event.starts_at).toLocaleString()}
          </p>

          <div className="mt-4 grid grid-cols-3 gap-2 border-t border-slate-800 pt-4 text-center">
            {/* counts seats se derive hote hain — WebSocket update aate hi
                ye apne aap badal jaate hain, server call ke bina */}
            <Stat value={counts.available || 0} label="Available" className="text-emerald-300" />
            <Stat value={counts.locked || 0} label="Held" className="text-amber-300" />
            <Stat value={counts.booked || 0} label="Booked" className="text-rose-300" />
          </div>
        </div>
      )}

      {/* Hold ki hui seat */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <h3 className="text-sm font-medium text-slate-400">Your Hold</h3>

        {selectedSeat ? (
          <>
            <div className="mt-2 flex items-baseline justify-between">
              <p className="text-2xl font-bold text-slate-100">
                {selectedSeat.row_label}-{selectedSeat.seat_number}
              </p>
              {/* Countdown — Redis TTL ka live reflection */}
              <span
                className={`rounded-md px-2 py-1 font-mono text-sm ${
                  urgent
                    ? 'bg-rose-950/60 text-rose-300'
                    : 'bg-slate-950/60 text-amber-300'
                }`}
              >
                {formatTime(lockSecondsLeft)}
              </span>
            </div>

            <p className="text-sm text-slate-400">₹{selectedSeat.price}</p>
            <p className="mt-1 text-xs text-slate-600">
              seat version {selectedSeat.version}
            </p>

            <p className="mt-3 text-xs text-slate-500">
              Ye seat tumhare naam hold hai. Time khatam hone par apne aap
              wapas available ho jayegi.
            </p>
          </>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            Grid me se koi hari seat chuno — wo turant tumhare naam hold ho jayegi
          </p>
        )}

        <button
          onClick={onBook}
          disabled={!selectedSeat || booking}
          className="mt-4 w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium
                     transition hover:bg-indigo-500 disabled:cursor-not-allowed
                     disabled:opacity-40"
        >
          {booking ? 'Booking…' : 'Confirm Booking'}
        </button>

        {selectedSeat && (
          <button
            onClick={onRelease}
            className="mt-2 w-full rounded-lg border border-slate-800 px-4 py-2 text-sm
                       text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
          >
            Release Hold
          </button>
        )}

        {message && (
          <p
            className={`mt-3 text-sm ${
              message.type === 'error' ? 'text-rose-300' : 'text-emerald-300'
            }`}
          >
            {message.text}
          </p>
        )}
      </div>

      {/* My bookings */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <h3 className="text-sm font-medium text-slate-400">
          My Bookings{' '}
          <span className="text-slate-600">
            ({bookings.filter((b) => b.status === 'confirmed').length})
          </span>
        </h3>

        {bookings.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">Abhi koi booking nahi</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {bookings.map((b) => (
              <li
                key={b.id}
                className="flex items-center justify-between rounded-lg bg-slate-950/60 px-3 py-2 text-sm"
              >
                <span className={b.status === 'cancelled' ? 'text-slate-600 line-through' : ''}>
                  <span className="font-medium text-slate-200">{b.seat_label}</span>
                  <span className="ml-2 text-slate-500">₹{b.amount}</span>
                </span>

                {b.status === 'confirmed' ? (
                  <button
                    onClick={() => onCancel(b.id)}
                    className="text-xs text-rose-400 transition hover:text-rose-300"
                  >
                    Cancel
                  </button>
                ) : (
                  <span className="text-xs text-slate-600">cancelled</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function Stat({ value, label, className }) {
  return (
    <div>
      <p className={`text-lg font-semibold ${className}`}>{value}</p>
      <p className="text-[11px] uppercase tracking-wide text-slate-600">{label}</p>
    </div>
  )
}
