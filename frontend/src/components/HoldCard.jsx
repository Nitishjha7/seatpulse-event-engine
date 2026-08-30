import { IconClock, IconLock } from '../layout/icons'

/** 125 -> "2:05" */
function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * Hold ki hui seat + countdown + confirm/release.
 *
 * Countdown Redis TTL ka reflection hai — asli expiry server pe hoti hai,
 * ye sirf user ko batata hai kitna time bacha.
 */
export default function HoldCard({
  seat,
  secondsLeft,
  onPay,
  onRelease,
  booking,
  message,
}) {
  // Aakhri minute me countdown laal — user ko jaldi karni chahiye
  const urgent = secondsLeft > 0 && secondsLeft <= 60

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <IconLock width={15} height={15} className="text-slate-500" />
          Your Hold
        </h2>

        {seat && (
          <span
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-mono text-sm
                        tabular-nums ${
                          urgent
                            ? 'bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/30'
                            : 'bg-amber-400/10 text-amber-300 ring-1 ring-amber-400/20'
                        }`}
          >
            <IconClock width={13} height={13} />
            {formatTime(secondsLeft)}
          </span>
        )}
      </div>

      {seat ? (
        <>
          <p className="mt-4 text-4xl font-bold tracking-tight text-white">
            {seat.row_label}-{seat.seat_number}
          </p>
          <p className="mt-0.5 text-lg text-slate-300">₹{seat.price}</p>

          {/* version dikha rahe hain kyunki optimistic locking isi par chalti hai —
              booking/hold ke baad ye number badalta hua dikhta hai */}
          <p className="mt-1 font-mono text-[11px] text-slate-600">
            seat version {seat.version}
          </p>

          <p className="mt-4 text-xs leading-relaxed text-slate-500">
            Ye seat tumhare naam hold hai. Booking payment complete hone par
            hi banegi — aur time khatam hone par seat apne aap wapas available
            ho jayegi, chahe tum browser band hi kyu na kar do.
          </p>

          <button
            onClick={onPay}
            disabled={booking}
            className="mt-4 w-full rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold
                       transition hover:bg-violet-500 disabled:cursor-not-allowed
                       disabled:opacity-50"
          >
            {booking ? 'Redirecting…' : `Pay ₹${seat.price}`}
          </button>

          <button
            onClick={onRelease}
            className="mt-2 w-full rounded-xl border border-[var(--border)] px-4 py-2.5
                       text-sm text-slate-400 transition hover:bg-white/5 hover:text-slate-200"
          >
            Release Hold
          </button>
        </>
      ) : (
        <div className="py-8 text-center">
          <p className="text-3xl">🎫</p>
          <p className="mt-2 text-sm text-slate-400">Koi seat hold nahi hai</p>
          <p className="mt-1 text-xs text-slate-600">
            Grid me se koi hari seat chuno — wo turant 5 minute ke liye
            tumhare naam ho jayegi
          </p>
        </div>
      )}

      {message && (
        <p
          className={`mt-3 rounded-lg px-3 py-2 text-sm ${
            message.type === 'error'
              ? 'bg-rose-500/10 text-rose-300'
              : 'bg-emerald-500/10 text-emerald-300'
          }`}
        >
          {message.text}
        </p>
      )}
    </section>
  )
}
