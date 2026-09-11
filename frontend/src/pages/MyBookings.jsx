import { useBooking } from '../booking/BookingContext'
import BookingsList from '../components/BookingsList'

export default function MyBookings() {
  const { bookings, cancel } = useBooking()

  const confirmed = bookings.filter((b) => b.status === 'confirmed')
  const spent = confirmed.reduce((sum, b) => sum + Number(b.amount), 0)

  return (
    <div className="animate-rise space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">My Bookings</h1>
        <p className="mt-1 text-sm text-slate-500">
          All bookings, including confirmed and cancelled.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat value={confirmed.length} label="Confirmed" className="text-emerald-400" />
        <Stat
          value={bookings.length - confirmed.length}
          label="Cancelled"
          className="text-slate-400"
        />
        <Stat value={`₹${spent}`} label="Total spent" className="text-violet-300" />
      </div>

      <BookingsList bookings={bookings} onCancel={cancel} />

      {bookings.some((b) => b.status === 'cancelled') && (
        <p className="text-xs text-slate-600">
          Cancelled bookings are retained for record-keeping. The partial unique index
          applies only to <code className="text-slate-500">confirmed</code> status,
          allowing the seat to be rebooked.
        </p>
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
