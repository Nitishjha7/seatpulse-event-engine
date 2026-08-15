import { IconCalendar, IconPin, IconTicket } from '../layout/icons'

/**
 * Event ka summary — live counts + details.
 *
 * Counts `seats` array se derive hote hain (BookingContext me), server se
 * nahi. Isliye WebSocket update aate hi ye apne aap sahi ho jaate hain.
 */
export default function EventSummary({ event, counts }) {
  if (!event) return null

  const starts = new Date(event.starts_at)

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <h2 className="text-sm font-medium text-slate-300">Event Summary</h2>

      <div className="mt-4 grid grid-cols-3 gap-2 text-center">
        <Stat value={counts.available || 0} label="Available" className="text-emerald-400" />
        <Stat value={counts.locked || 0} label="Held" className="text-amber-400" />
        <Stat value={counts.booked || 0} label="Booked" className="text-rose-400" />
      </div>

      <div className="mt-4 space-y-1 border-t border-[var(--border)] pt-3">
        <Row Icon={IconPin} label="Venue" value={event.venue} />
        <Row
          Icon={IconCalendar}
          label="Date & Time"
          value={`${starts.toLocaleDateString(undefined, {
            day: 'numeric',
            month: 'short',
            year: 'numeric',
          })}, ${starts.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
          })}`}
        />
        <Row Icon={IconTicket} label="Total Seats" value={event.total_seats} />
      </div>
    </section>
  )
}

function Stat({ value, label, className }) {
  return (
    <div className="rounded-xl bg-[var(--panel-2)] py-3">
      <p className={`text-2xl font-bold tabular-nums ${className}`}>{value}</p>
      <p className="mt-0.5 text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  )
}

function Row({ Icon, label, value }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <Icon width={16} height={16} className="shrink-0 text-slate-500" />
      <div className="min-w-0 flex-1">
        <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
        <p className="truncate text-sm text-slate-300">{value}</p>
      </div>
    </div>
  )
}
