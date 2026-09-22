/**
 * Live indicator for demand-based pricing.
 *
 * Deliberately skips the "Only 3 left!" fake-urgency pattern — every number
 * here is real (surge %, sold/total, seats until next increase). If
 * `seats_until_increase` is null we just hide the line instead of guessing.
 */
export default function PricingBanner({ pricing }) {
  // Dynamic pricing is off by default — nothing to show for most events.
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
            {sold} / {total} seats sold
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

      {/* Sold-out bar — this is what the price is actually tied to */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-amber-400
                     transition-[width] duration-500"
          style={{ width: `${soldPercent}%` }}
        />
      </div>

      {/* null = hide the line */}
      {until != null && (
        <p className="mt-2.5 text-xs text-slate-400">
          {until === 1
            ? 'Price will increase after one more booking'
            : `Price will increase after ${until} more seats are sold`}
        </p>
      )}

      <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
        The price is locked once you hold a seat — regardless of how many
        seats are sold in the meantime, you will pay the price you saw.
      </p>
    </section>
  )
}
