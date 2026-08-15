/**
 * Seat grid — event ki saari seats rows me.
 *
 * Phase 5 me isme WebSocket updates aayenge: dusra user seat lega to
 * yahan turant color badal jayega, bina refresh ke.
 */

// Har status ka apna look. Ek jagah rakha hai taki grid aur legend
// kabhi alag na dikhein.
const SEAT_STYLES = {
  available: 'bg-emerald-600/80 hover:bg-emerald-500 text-white cursor-pointer',
  locked: 'bg-amber-500/80 text-white cursor-not-allowed',
  booked: 'bg-rose-900/60 text-rose-300/60 cursor-not-allowed line-through',
  selected: 'bg-indigo-500 text-white ring-2 ring-indigo-300 cursor-pointer',
}

function Seat({ seat, isSelected, onSelect }) {
  const clickable = seat.status === 'available'
  const style = isSelected ? SEAT_STYLES.selected : SEAT_STYLES[seat.status]

  return (
    <button
      onClick={() => clickable && onSelect(seat)}
      disabled={!clickable}
      title={`${seat.row_label}-${seat.seat_number} · ₹${seat.price} · ${seat.status}`}
      className={`h-8 w-8 rounded text-[11px] font-medium transition ${style}`}
    >
      {seat.seat_number}
    </button>
  )
}

export default function SeatGrid({ seats, selectedSeat, onSelect }) {
  // Flat list ko rows me todo: { A: [...], B: [...] }
  // Backend already sorted bhej raha hai, isliye order sahi rahega.
  const rows = seats.reduce((acc, seat) => {
    ;(acc[seat.row_label] ||= []).push(seat)
    return acc
  }, {})

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
      {/* Stage — bina iske samajh nahi aata ki aage kaunsi taraf hai */}
      <div className="mx-auto mb-6 w-2/3 rounded-b-2xl border-b-2 border-slate-700 pb-2 text-center text-xs uppercase tracking-widest text-slate-500">
        Stage
      </div>

      <div className="space-y-1.5 overflow-x-auto">
        {Object.entries(rows).map(([rowLabel, rowSeats]) => (
          <div key={rowLabel} className="flex items-center gap-1.5">
            <span className="w-5 shrink-0 text-xs font-semibold text-slate-500">
              {rowLabel}
            </span>
            {rowSeats.map((seat) => (
              <Seat
                key={seat.id}
                seat={seat}
                isSelected={selectedSeat?.id === seat.id}
                onSelect={onSelect}
              />
            ))}
          </div>
        ))}
      </div>

      <div className="mt-6 flex flex-wrap gap-4 border-t border-slate-800 pt-4 text-xs text-slate-400">
        <Legend className="bg-emerald-600/80" label="Available" />
        <Legend className="bg-indigo-500" label="Selected" />
        <Legend className="bg-amber-500/80" label="Locked" />
        <Legend className="bg-rose-900/60" label="Booked" />
      </div>
    </div>
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
