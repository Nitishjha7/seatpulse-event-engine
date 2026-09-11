/**
 * Live indicator for demand-based pricing.
 *
 * ---- Crucial decision: what NOT to show ----
 *
 * Ticketing sites often display "Only 3 left!" or "🔥 Selling fast!"
 * even if 300 seats are available. This is dishonest and erodes
 * trust in the entire product.
 *
 * Every number here is server-sourced and accurate:
 *   - surge_percent          -> current multiplier, calculated
 *   - sold / total           -> actual count
 *   - seats_until_increase   -> derived from actual loop, not an estimate
 *
 * If `seats_until_increase` is null (price won't increase, or
 * max surge reached), we hide the line entirely — better to have
 * empty space than create false urgency.
 */
export default function PricingBanner({ pricing }) {
  // Hide component if dynamic pricing is disabled. This is the default,
  // and discussing surge pricing for such events feels inappropriate.
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

      {/* Sold-out bar. This number determines the price —
          users should see what the price is tied to. */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-amber-400
                     transition-[width] duration-500"
          style={{ width: `${soldPercent}%` }}
        />
      </div>

      {/* Only show if data is verified. null = hide. */}
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
