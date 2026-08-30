/**
 * Demand-based pricing ka live indicator.
 *
 * ---- Yahan sabse zaroori faisla: kya NAHI dikhana ----
 *
 * Ticketing sites yahan "Only 3 left!" aur "🔥 Selling fast!" chipka deti
 * hain, chahe 300 seats khaali padi hon. Wo jhooth hai, aur ek baar pakda
 * jaye to poore product ka bharosa uth jata hai.
 *
 * Yahan har number server se aata hai aur sach hai:
 *   - surge_percent          -> abhi ka multiplier, calculated
 *   - sold / total           -> asli ginti
 *   - seats_until_increase   -> asli loop se nikala hua, andaza nahi
 *
 * Aur agar `seats_until_increase` null hai (price abhi nahi badhega, ya
 * max surge aa chuka), to hum us line ko DIKHATE HI NAHI — jhoothi
 * urgency banane se behtar hai khaali jagah.
 */
export default function PricingBanner({ pricing }) {
  // Dynamic pricing off hai to poora component gayab. Ye default hai, aur
  // aise events pe surge ki baat karna hi galat lagta.
  if (!pricing?.enabled) return null

  const { surge_percent: surge, sold, total, seats_until_increase: until } = pricing
  const soldPercent = total > 0 ? Math.round((sold / total) * 100) : 0

  return (
    <section
      className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4"
      aria-live="polite"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-medium text-slate-300">
            📈 Demand pricing
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            {sold} / {total} seats bik chuki hain
          </p>
        </div>

        <span
          className={`shrink-0 rounded-lg px-2.5 py-1 text-sm font-semibold tabular-nums
                      ring-1 ${
                        surge > 0
                          ? 'bg-amber-400/10 text-amber-300 ring-amber-400/20'
                          : 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/20'
                      }`}
        >
          {surge > 0 ? `+${surge}%` : 'Base price'}
        </span>
      </div>

      {/* Sold-out bar. Yahi wo number hai jisse price nikalta hai —
          user ko dikhna chahiye ki price kis cheez se juda hai. */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-amber-400
                     transition-[width] duration-500"
          style={{ width: `${soldPercent}%` }}
        />
      </div>

      {/* Sirf tab jab sach me pata ho. null = mat dikhao. */}
      {until != null && (
        <p className="mt-2.5 text-xs text-slate-400">
          {until === 1
            ? 'Ek aur booking pe price badh jayega'
            : `${until} aur seats bikne pe price badhega`}
        </p>
      )}

      <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
        Seat hold karte hi uska price lock ho jata hai — beech me kitni bhi
        seats bik jayein, tumse wahi liya jayega jo tumne dekha tha.
      </p>
    </section>
  )
}
