import { useState } from 'react'

import { searchSeats } from '../api'
import { useAuth } from '../auth/AuthContext'

/**
 * Natural language seat search.
 *
 * ---- Sabse zaroori UX faisla: interpretation DIKHAO ----
 *
 * User likhta hai "3 seats together under 1500 near the stage" aur usse
 * 0 results milte hain. Ab wo kya kare?
 *
 * Agar hum sirf "kuch nahi mila" dikhayein to usse pata hi nahi chalega
 * ki galti kahan hui — usne galat likha, ya seats sach me nahi hain, ya
 * AI ne kuch aur samajh liya.
 *
 * Isliye hum hamesha dikhate hain ki query ka kya MATLAB nikala gaya:
 * "3 seats · saath me · ₹1500 tak · aage". Ab user turant dekh leta hai
 * ki "1500" ko max_price samjha gaya ya nahi.
 *
 * ---- Aur ek: ye component AI ke bina bhi kaam karta hai ----
 *
 * `aiSearchEnabled` false ho to search box render hi nahi hota. Wahi
 * pattern jo Google login button ka hai — feature na ho to wo gayab ho,
 * toota hua na dikhe.
 */
export default function SeatSearch({ eventId, onPick }) {
  const { aiSearchEnabled } = useAuth()

  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // Key nahi hai — box dikhao hi mat.
  if (!aiSearchEnabled) return null

  async function run(e) {
    e.preventDefault()
    if (!query.trim() || busy) return

    setBusy(true)
    setError(null)
    try {
      setResult(await searchSeats(eventId, { query }))
    } catch (err) {
      setError(err.message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4">
      <form onSubmit={run}>
        <label className="block text-sm font-medium text-slate-300">
          Seats dhoondo
        </label>
        <p className="mt-0.5 text-xs text-slate-600">
          Apne shabdon me likho — "3 seats saath me, 1500 se kam, stage ke paas"
        </p>

        <div className="mt-2 flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={200}
            placeholder="2 seats together under ₹2000"
            className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2
                       text-sm text-slate-100 outline-none transition
                       placeholder:text-slate-700 focus:border-violet-500"
          />
          <button
            type="submit"
            disabled={busy || !query.trim()}
            className="rounded-lg bg-violet-600 px-4 text-sm font-medium transition
                       hover:bg-violet-500 disabled:opacity-40"
          >
            {busy ? '…' : 'Search'}
          </button>
        </div>
      </form>

      {error && (
        <p className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      {result && <Results result={result} onPick={onPick} />}
    </section>
  )
}

function Results({ result, onPick }) {
  const f = result.filters

  // Query ka kya matlab nikala gaya — user ko dikhna chahiye
  const chips = [
    f.quantity > 1 && `${f.quantity} seats`,
    f.quantity > 1 && (f.together ? 'saath me' : 'alag-alag chalega'),
    f.max_price != null && `₹${f.max_price} tak`,
    f.min_price != null && `₹${f.min_price} se upar`,
    f.section && f.section,
    f.row_preference === 'front' && 'aage',
    f.row_preference === 'back' && 'peeche',
  ].filter(Boolean)

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {!result.interpreted && (
          // Imaandari: AI ne query samjhi hi nahi, to ye mat jataao ki
          // samajh ke result diya hai
          <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-300">
            Query samajh nahi aayi — saari available seats dikha rahe hain
          </span>
        )}
        {chips.map((c) => (
          <span
            key={c}
            className="rounded bg-violet-500/10 px-2 py-0.5 text-[11px] text-violet-300"
          >
            {c}
          </span>
        ))}
      </div>

      {result.matches.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          In filters pe kuch nahi mila. Kam seats ya zyada budget try karo.
        </p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {result.matches.slice(0, 6).map((m) => (
            <li key={m.seat_ids.join('-')}>
              <button
                onClick={() => onPick?.(m)}
                className="flex w-full items-center justify-between rounded-lg border
                           border-[var(--border)] px-3 py-2 text-left transition
                           hover:border-violet-500/50 hover:bg-white/5"
              >
                <span className="text-sm text-slate-200">
                  {m.label}
                  {m.section && (
                    <span className="ml-1.5 text-xs text-slate-500">{m.section}</span>
                  )}
                </span>
                <span className="text-sm font-medium text-emerald-300">
                  ₹{m.total_price}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
