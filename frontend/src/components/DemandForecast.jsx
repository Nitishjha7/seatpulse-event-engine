import { useState } from 'react'

import { getForecast } from '../api'

/**
 * Sellout projection from the event's own booking pace — a linear
 * extrapolation (see backend/services/forecast.py), not a trained model.
 * Loaded on demand rather than with the event list, since it's a
 * per-event query and most events won't have this panel open at once.
 */
export default function DemandForecast({ eventId }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [forecast, setForecast] = useState(null)

  async function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (forecast || error) return   // already loaded once

    setBusy(true)
    try {
      setForecast(await getForecast(eventId))
    } catch (err) {
      // A 404 here means "not enough bookings yet" — expected for a new
      // event, not a failure.
      setError(err.status === 404 ? 'not-enough-data' : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={toggle}
        className="text-xs text-violet-300 underline decoration-violet-500/40"
      >
        {open ? 'Hide forecast' : 'Sellout forecast'}
      </button>

      {open && (
        <div className="mt-2 rounded-lg border border-[var(--border)] bg-white/[0.02] px-3 py-2 text-xs">
          {busy && <p className="text-slate-500">Calculating…</p>}

          {error === 'not-enough-data' && (
            <p className="text-slate-500">
              Not enough bookings yet to project a trend.
            </p>
          )}

          {error && error !== 'not-enough-data' && (
            <p className="text-rose-300">{error}</p>
          )}

          {forecast && (
            <div className="space-y-1 text-slate-400">
              <p>
                Pace: <strong className="text-slate-200">{forecast.rate_per_day.toFixed(1)}</strong> bookings/day
                {' '}(from {forecast.bookings_analyzed} bookings)
              </p>
              {forecast.sellout_date ? (
                <p>
                  At this pace, projected to sell out around{' '}
                  <strong className="text-emerald-400">
                    {new Date(forecast.sellout_date).toLocaleDateString(undefined, {
                      day: 'numeric', month: 'short', year: 'numeric',
                    })}
                  </strong>
                </p>
              ) : (
                <p>
                  At this pace, projected{' '}
                  <strong className="text-violet-300">{forecast.projected_percent_sold}%</strong>
                  {' '}sold by the event date
                </p>
              )}
              <p className="text-[10px] text-slate-600">
                Estimate from this event's own booking history — not a prediction across events.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
